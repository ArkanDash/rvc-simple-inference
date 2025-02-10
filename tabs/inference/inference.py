import os, sys, subprocess, logging, json
from logging.handlers import RotatingFileHandler
from contextlib import suppress

import yt_dlp, gradio as gr, librosa, numpy as np, soundfile as sf
from pydub import AudioSegment

try:
    from audio_separator.separator import Separator
except ImportError:
    raise ImportError("Ensure the 'audio_separator' module is installed or in your working directory.")

# --- Logging Setup ---
def setup_logging(level=logging.DEBUG, log_file="kuro_rvc.log"):
    logger = logging.getLogger()
    logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    fh = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2)
    fh.setLevel(level)
    fh.setFormatter(formatter)
    logger.handlers.clear()
    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.debug("Logging configured.")
setup_logging()

# --- Global Paths ---
current_dir = os.getcwd()
rvc_models_dir = os.path.join(current_dir, 'logs')
rvc_output_dir = os.path.join(current_dir, 'song_output')
download_dir = os.path.join(current_dir, "downloads")
uvr_output_dir = os.path.join(current_dir, "output_uvr")
rvc_cli_file = os.path.join(current_dir, "scrpt.py")
if not os.path.exists(rvc_cli_file):
    logging.error("scrpt.py not found in %s", current_dir)
    raise FileNotFoundError("scrpt.py not found in current directory.")

# --- Helper Functions ---
def download_youtube_audio(url, download_dir):
    os.makedirs(download_dir, exist_ok=True)
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(download_dir, "%(title)s.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav", "preferredquality": "192"}],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if "entries" in info:
        files = [os.path.join(download_dir, f"{entry['title']}.wav") for entry in info["entries"] if entry]
    else:
        files = os.path.join(download_dir, f"{info['title']}.wav")
    logging.debug("Downloaded: %s", files)
    return files

def separator_uvr(input_audio, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    sep = Separator(output_dir=output_dir)
    sep.load_model('model_bs_roformer_ep_317_sdr_12.9755.ckpt')
    sep_files = sep.separate(input_audio)
    if len(sep_files) < 2: raise RuntimeError("UVR separation failed (instrumental/vocals).")
    instrumental = os.path.join(output_dir, 'Instrumental.wav')
    vocals = os.path.join(output_dir, 'Vocals.wav')
    os.rename(os.path.join(output_dir, sep_files[0]), instrumental)
    os.rename(os.path.join(output_dir, sep_files[1]), vocals)
    sep.load_model('mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt')
    sep_vocals = sep.separate(vocals)
    if len(sep_vocals) < 2: raise RuntimeError("UVR separation failed (vocal split).")
    backing = os.path.join(output_dir, 'Backing_Vocals.wav')
    lead = os.path.join(output_dir, 'Lead_Vocals.wav')
    os.rename(os.path.join(output_dir, sep_vocals[0]), backing)
    os.rename(os.path.join(output_dir, sep_vocals[1]), lead)
    return lead, backing, instrumental

def run_rvc(f0_up_key, filter_radius, rms_mix_rate, index_rate, hop_length, protect,
            f0_method, input_path, output_path, pth_file, index_file, split_audio,
            clean_audio, clean_strength, export_format, f0_autotune,
            embedder_model, embedder_model_custom, rvc_cli_file):
    cmd = [
        sys.executable, rvc_cli_file, "infer",
        "--pitch", str(f0_up_key),
        "--filter_radius", str(filter_radius),
        "--volume_envelope", str(rms_mix_rate),
        "--index_rate", str(index_rate),
        "--hop_length", str(hop_length),
        "--protect", str(protect),
        "--f0_method", f0_method,
        "--f0_autotune", str(f0_autotune),
        "--input_path", input_path,
        "--output_path", output_path,
        "--pth_path", pth_file,
        "--index_path", index_file,
        "--split_audio", str(split_audio),
        "--clean_audio", str(clean_audio),
        "--clean_strength", str(clean_strength),
        "--export_format", export_format,
        "--embedder_model", embedder_model,
        "--embedder_model_custom", embedder_model_custom
    ]
    logging.info("Running RVC inference: %s", " ".join(cmd))
    subprocess.run(cmd, check=True, capture_output=True, text=True)

def load_audio(file_path):
    if file_path and os.path.exists(file_path):
        return AudioSegment.from_file(file_path)
    logging.warning("Audio file not found: %s", file_path)
    return None

def run_advanced_rvc(model_name, youtube_url, export_format, f0_method, f0_up_key, filter_radius,
                     rms_mix_rate, protect, index_rate, hop_length, clean_strength, split_audio,
                     clean_audio, f0_autotune, backing_vocal_infer, embedder_model, embedder_model_custom):
    try:
        current_dir = os.getcwd()
        rvc_models_dir = os.path.join(current_dir, 'logs')
        rvc_output_dir = os.path.join(current_dir, 'song_output')
        download_dir = os.path.join(current_dir, "downloads")
        uvr_output_dir = os.path.join(current_dir, "output_uvr")
        rvc_cli_file = os.path.join(current_dir, "scrpt.py")
        if not os.path.exists(rvc_cli_file):
            return f"scrpt.py not found in {current_dir}", None
        
        model_folder = os.path.join(rvc_models_dir, model_name)
        if not os.path.exists(model_folder):
            return f"Model directory not found: {model_folder}", None
        files = os.listdir(model_folder)
        pth_file = os.path.join(model_folder, next((f for f in files if f.endswith(".pth")), ""))
        index_file = os.path.join(model_folder, next((f for f in files if f.endswith(".index")), ""))
        if not os.path.exists(pth_file) or not os.path.exists(index_file):
            return "Required model files (.pth or .index) not found.", None
        
        downloaded = download_youtube_audio(youtube_url, download_dir)
        input_audio = downloaded[0] if isinstance(downloaded, list) else downloaded
        if not os.path.exists(input_audio):
            return f"Downloaded audio file not found: {input_audio}", None
        
        lead, backing, instrumental = separator_uvr(input_audio, uvr_output_dir)
        os.makedirs(rvc_output_dir, exist_ok=True)
        rvc_lead = os.path.join(rvc_output_dir, "rvc_result_lead.wav")
        rvc_backing = os.path.join(rvc_output_dir, "rvc_result_backing.wav")
        run_rvc(f0_up_key, filter_radius, rms_mix_rate, index_rate, hop_length, protect,
                f0_method, lead, rvc_lead, pth_file, index_file,
                split_audio, clean_audio, clean_strength, export_format, f0_autotune,
                embedder_model, embedder_model_custom, rvc_cli_file)
        if backing_vocal_infer:
            run_rvc(f0_up_key, filter_radius, rms_mix_rate, index_rate, hop_length, protect,
                    f0_method, backing, rvc_backing, pth_file, index_file,
                    split_audio, clean_audio, clean_strength, export_format, f0_autotune,
                    embedder_model, embedder_model_custom, rvc_cli_file)
        lead_audio = load_audio(rvc_lead)
        instrumental_audio = load_audio(instrumental)
        backing_audio = load_audio(rvc_backing) if backing_vocal_infer else load_audio(backing)
        if not instrumental_audio:
            return "Instrumental track is required for mixing!", None
        final_mix = instrumental_audio.overlay(lead_audio) if lead_audio else instrumental_audio
        if backing_audio:
            final_mix = final_mix.overlay(backing_audio)
        output_file = f"aicover_{model_name}.{export_format.lower()}"
        final_mix.export(output_file, format=export_format.lower())
        return f"Mixed file saved as: {output_file}", output_file, lead_audio, backing_audio
    except Exception as e:
        logging.exception("Error during pipeline: %s", e)
        return f"An error occurred: {e}", None

# --- Gradio UI ---
def inference_tab():
    with gr.Tabs():
        with gr.Row():
            model_name_input = gr.Textbox(label="Model Name", value="Sonic")
            youtube_url_input = gr.Textbox(label="YouTube URL", value="https://youtu.be/eCkWlRL3_N0?si=y6xHAs1m8fYVLTUV")
            export_format_input = gr.Dropdown(label="Export Format", choices=["WAV", "MP3", "FLAC", "OGG", "M4A"], value="WAV")
            f0_method_input = gr.Dropdown(label="F0 Method", choices=["crepe", "crepe-tiny", "rmvpe", "fcpe", "hybrid[rmvpe+fcpe]"], value="hybrid[rmvpe+fcpe]")
        with gr.Row():
            f0_up_key_input = gr.Slider(label="F0 Up Key", minimum=-24, maximum=24, step=1, value=0)
            filter_radius_input = gr.Slider(label="Filter Radius", minimum=0, maximum=10, step=1, value=3)
            rms_mix_rate_input = gr.Slider(label="RMS Mix Rate", minimum=0.0, maximum=1.0, step=0.1, value=0.8)
            protect_input = gr.Slider(label="Protect", minimum=0.0, maximum=0.5, step=0.1, value=0.5)
        with gr.Row():
            index_rate_input = gr.Slider(label="Index Rate", minimum=0.0, maximum=1.0, step=0.1, value=0.6)
            hop_length_input = gr.Slider(label="Hop Length", minimum=1, maximum=512, step=1, value=128)
            clean_strength_input = gr.Slider(label="Clean Strength", minimum=0.0, maximum=1.0, step=0.1, value=0.7)
            split_audio_input = gr.Checkbox(label="Split Audio", value=False)
        with gr.Row():
            clean_audio_input = gr.Checkbox(label="Clean Audio", value=False)
            f0_autotune_input = gr.Checkbox(label="F0 Autotune", value=False)
            backing_vocal_infer_input = gr.Checkbox(label="Infer Backing Vocals", value=False)
        with gr.Row():
            embedder_model_input = gr.Dropdown(label="Embedder Model",
                                               choices=["contentvec", "chinese-hubert-base", "japanese-hubert-base", "korean-hubert-base", "custom"],
                                               value="contentvec")
            embedder_model_custom_input = gr.Textbox(label="Custom Embedder Model", value="")
        run_button = gr.Button("Convert")
        output_message = gr.Textbox(label="Status")
        output_audio = gr.Audio(label="Final Mixed Audio", type="filepath")
        output_lead = gr.Audio(label="Output Lead Ai Cover:", type="filepath")
        output_backing = gr.Audio(label="Output Backing Ai Cover:", type="filepath")
        run_button.click(
            run_advanced_rvc,
            inputs=[model_name_input, youtube_url_input, export_format_input, f0_method_input,
                    f0_up_key_input, filter_radius_input, rms_mix_rate_input, protect_input,
                    index_rate_input, hop_length_input, clean_strength_input, split_audio_input,
                    clean_audio_input, f0_autotune_input, backing_vocal_infer_input,
                    embedder_model_input, embedder_model_custom_input],
            outputs=[output_message, output_audio, output_lead, output_backing]
        )
    
