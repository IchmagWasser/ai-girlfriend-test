from TTS.api import TTS
import sounddevice as sd
import soundfile as sf
import tempfile
import os

# Nutzungsbedingungen zustimmen (Pflicht bei Coqui XTTS)
os.environ["COQUI_TOS_AGREED"] = "1"

# TTS-Modell laden (kein GPU-Modus)
tts = TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False, gpu=False)

# Sprach-Ausgabe-Funktion
def speak(text, speaker_wav=None, language="de"):
    # Temporäre Datei für Audiodatei erzeugen
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fp:
        # Text zu Sprache konvertieren (optional mit Referenz-Stimme)
        tts.tts_to_file(text=text, speaker_wav=speaker_wav, language=language, file_path=fp.name)
        fp.close()

        # Datei abspielen
        data, samplerate = sf.read(fp.name)
        sd.play(data, samplerate)
        sd.wait()
        
        # Temporäre Datei löschen
        os.remove(fp.name)
