#!/usr/bin/env python3
"""Dublagem gratuita em PT-BR com duas vozes.

Uso:
  python dublar.py LINK_DO_YOUTUBE_ou_ARQUIVO [--saida pasta] [--modelo small]

Etapas (tudo gratuito):
  0. Baixa o vídeo com yt-dlp (se receber um link).
  1. Transcreve com faster-whisper (roda no computador).
  2. Separa em 2 falantes pelo timbre da voz (MFCC + tom + KMeans).
  3. Traduz para português (Google Tradutor gratuito; reserva: MyMemory).
  4. Gera as vozes com edge-tts: Antonio (Voz 1) e Francisca (Voz 2).
  5. Encaixa cada fala no tempo original e monta o vídeo dublado + legendas.
O ffmpeg vem embutido (imageio-ffmpeg); não precisa instalar à parte.
"""
import argparse, asyncio, json, re, subprocess, sys, time
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
VOZES = ["pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"]


def ff(*args):
    r = subprocess.run([FFMPEG, "-hide_banner", "-y", *args], capture_output=True, text=True)
    if r.returncode:
        sys.exit("Erro no ffmpeg:\n" + r.stderr[-1500:])


def duracao(arq):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(arq)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def baixar(link, pasta):
    import yt_dlp
    print("Baixando o vídeo...")
    opts = {"format": "bv*[height<=720]+ba/b[height<=720]/b", "merge_output_format": "mp4",
            "outtmpl": str(pasta / "video.%(ext)s"), "ffmpeg_location": FFMPEG,
            "noplaylist": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(link, download=True)
    (pasta / "info.txt").write_text(f"{info.get('title')}\n{info.get('uploader')}\n"
                                    f"{info.get('description') or ''}", encoding="utf-8")
    return next(pasta.glob("video.*"))


def transcrever(audio, modelo, idioma):
    from faster_whisper import WhisperModel
    print(f"Transcrevendo (modelo {modelo}; pode demorar)...")
    m = WhisperModel(modelo, device="cpu", compute_type="int8")
    import soundfile as sf
    onda, _ = sf.read(str(audio), dtype="float32")  # 16 kHz mono
    segs, info = m.transcribe(onda, language=idioma, vad_filter=True)
    out = []
    for s in segs:
        out.append({"ini": round(s.start, 2), "fim": round(s.end, 2), "texto": s.text.strip()})
        print(f"\r  {s.end/60:6.1f} min transcritos", end="", flush=True)
    print(f"\nIdioma: {info.language} | {len(out)} trechos")
    return out, info.language


def separar_falantes(audio, segs):
    import numpy as np, librosa
    from sklearn.cluster import KMeans
    print("Separando as duas vozes...")
    y, sr = librosa.load(str(audio), sr=16000)
    feats = []
    for s in segs:
        t = y[int(s["ini"] * sr):int(s["fim"] * sr)]
        if len(t) < sr // 2:
            t = np.pad(t, (0, sr // 2 - len(t)))
        mf = librosa.feature.mfcc(y=t, sr=sr, n_mfcc=20)
        f0 = librosa.yin(t, fmin=60, fmax=400, sr=sr)
        feats.append(np.concatenate([mf.mean(1)[1:], mf.std(1)[1:], [np.median(f0)] * 3]))
    X = np.array(feats)
    X = (X - X.mean(0)) / (X.std(0) + 1e-9)
    rot = KMeans(n_clusters=2, n_init=10, random_state=0).fit_predict(X)
    # Voz 1 = quem fala primeiro
    if rot[0] == 1:
        rot = 1 - rot
    for s, r in zip(segs, rot):
        s["falante"] = int(r)


def traduzir(segs, origem):
    from deep_translator import GoogleTranslator, MyMemoryTranslator
    if origem == "pt":
        for s in segs:
            s["pt"] = s["texto"]
        return
    print("Traduzindo para português...")
    g = GoogleTranslator(source="auto", target="pt")
    for i, s in enumerate(segs):
        for tentativa in range(5):
            try:
                s["pt"] = g.translate(s["texto"]) or s["texto"]
                break
            except Exception:
                time.sleep(2 * (tentativa + 1))
        else:
            try:
                s["pt"] = MyMemoryTranslator(source=origem, target="pt-BR").translate(s["texto"])
            except Exception:
                s["pt"] = s["texto"]
        print(f"\r  {i+1}/{len(segs)}", end="", flush=True)
        time.sleep(0.25)
    print()


async def sintetizar(segs, pasta):
    import edge_tts
    print("Gerando as vozes em português...")
    for i, s in enumerate(segs):
        wav = pasta / f"{i:05d}.wav"
        if not wav.exists():
            mp3 = pasta / f"{i:05d}.mp3"
            for tentativa in range(4):
                try:
                    await edge_tts.Communicate(s["pt"], VOZES[s["falante"]]).save(str(mp3))
                    break
                except Exception:
                    await asyncio.sleep(3 * (tentativa + 1))
            else:
                sys.exit("Não consegui gerar a voz (verifique a internet) e rode de novo; "
                         "o que já foi feito fica guardado.")
            ff("-i", str(mp3), "-ac", "1", "-ar", "24000", str(wav))
            mp3.unlink()
        s["wav"] = str(wav)
        print(f"\r  {i+1}/{len(segs)}", end="", flush=True)
    print()


def montar(video, segs, pasta, saida):
    import numpy as np, soundfile as sf
    print("Montando o vídeo dublado...")
    sr = 24000
    total = duracao(video)
    trilha = np.zeros(int((total + 30) * sr), dtype=np.float32)
    for k, s in enumerate(segs):
        dados, _ = sf.read(s["wav"], dtype="float32")
        # tempo disponível: até a próxima fala (ou o fim do trecho, se maior)
        prox = segs[k + 1]["ini"] if k + 1 < len(segs) else total
        janela = max(prox - s["ini"], s["fim"] - s["ini"], 0.5)
        vel = len(dados) / sr / janela
        if vel > 1.0:
            rapido = pasta / f"r{k:05d}.wav"
            ff("-i", s["wav"], "-filter:a", f"atempo={min(vel, 1.7):.3f}", str(rapido))
            dados, _ = sf.read(str(rapido), dtype="float32")
            rapido.unlink()
        a = int(s["ini"] * sr)
        trilha[a:a + len(dados)] += dados[:len(trilha) - a]
    trilha = trilha[:int(total * sr)]
    sf.write(str(saida / "audio_dublado.wav"), np.clip(trilha, -1, 1), sr)
    ff("-i", str(video), "-i", str(saida / "audio_dublado.wav"), "-filter_complex",
       "[0:a]volume=0.10[bg];[bg][1:a]amix=inputs=2:normalize=0:duration=first[a]",
       "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
       str(saida / "video_dublado.mp4"))
    ff("-i", str(saida / "audio_dublado.wav"), "-b:a", "128k", str(saida / "audio_dublado.mp3"))
    (saida / "audio_dublado.wav").unlink()


def tempo(x):
    h, r = divmod(x, 3600); m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"


def escrever_textos(segs, saida):
    for campo, nome in (("texto", "legendas_original.srt"), ("pt", "legendas_pt.srt")):
        with open(saida / nome, "w", encoding="utf-8") as f:
            for i, s in enumerate(segs, 1):
                f.write(f"{i}\n{tempo(s['ini'])} --> {tempo(s['fim'])}\n"
                        f"[Voz {s['falante']+1}] {s[campo]}\n\n")
    # roteiro corrido em PT, agrupando falas seguidas do mesmo falante
    with open(saida / "roteiro_pt.txt", "w", encoding="utf-8") as f:
        atual = None
        for s in segs:
            if s["falante"] != atual:
                atual = s["falante"]
                f.write(f"\n[{tempo(s['ini'])[:8]}] VOZ {atual+1}: ")
            f.write(s["pt"] + " ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entrada", help="link do YouTube ou arquivo de vídeo")
    ap.add_argument("--saida", default="saida")
    ap.add_argument("--modelo", default="small", help="tiny/base/small/medium")
    ap.add_argument("--idioma", default=None, help="idioma falado, ex.: en (opcional)")
    a = ap.parse_args()
    saida = Path(a.saida); pasta = saida / "trabalho"
    pasta.mkdir(parents=True, exist_ok=True)

    if re.match(r"https?://", a.entrada):
        video = next(pasta.glob("video.*"), None) or baixar(a.entrada, pasta)
    else:
        video = Path(a.entrada)

    cache = pasta / "segmentos.json"
    if cache.exists():
        segs, lang = json.loads(cache.read_text(encoding="utf-8"))
    else:
        audio = pasta / "audio16k.wav"
        ff("-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio))
        segs, lang = transcrever(audio, a.modelo, a.idioma)
        separar_falantes(audio, segs)
        traduzir(segs, lang)
        cache.write_text(json.dumps([segs, lang], ensure_ascii=False, indent=1), encoding="utf-8")
    escrever_textos(segs, saida)
    asyncio.run(sintetizar(segs, pasta))
    montar(video, segs, pasta, saida)
    print("\nPRONTO! Arquivos em:", saida.resolve())


if __name__ == "__main__":
    main()
