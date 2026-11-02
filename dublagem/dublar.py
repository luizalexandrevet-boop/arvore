#!/usr/bin/env python3
"""Dublagem gratuita em PT-BR com duas vozes.

Etapas (tudo com ferramentas gratuitas):
  1. Transcrição com faster-whisper (local).
  2. Separação de falantes: agrupa os trechos em 2 vozes por características
     do áudio (MFCC + KMeans) — sem token pago.
  3. Tradução para português com Argos Translate (offline, gratuito).
  4. Síntese de voz com edge-tts (gratuito): uma voz masculina e uma feminina.
  5. Montagem: cada fala é encaixada no tempo original (acelerada se precisar)
     e o resultado é mixado sobre o vídeo, gerando também legendas .srt.

Uso:
  python3 dublar.py VIDEO.mp4 [--saida pasta] [--modelo small] [--idioma en]
"""
import argparse, asyncio, json, os, subprocess, sys
from pathlib import Path

VOZES = ["pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"]


def sh(*cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def transcrever(audio, modelo, idioma):
    from faster_whisper import WhisperModel
    m = WhisperModel(modelo, device="cpu", compute_type="int8")
    segs, info = m.transcribe(str(audio), language=idioma, vad_filter=True)
    out = [{"ini": s.start, "fim": s.end, "texto": s.text.strip()} for s in segs]
    print(f"Idioma detectado: {info.language} | {len(out)} trechos")
    return out, info.language


def separar_falantes(audio, segs):
    import numpy as np, librosa
    from sklearn.cluster import KMeans
    y, sr = librosa.load(str(audio), sr=16000)
    feats = []
    for s in segs:
        trecho = y[int(s["ini"] * sr):int(s["fim"] * sr)]
        if len(trecho) < sr // 4:
            trecho = np.pad(trecho, (0, sr // 4 - len(trecho)))
        mf = librosa.feature.mfcc(y=trecho, sr=sr, n_mfcc=20)
        f0 = librosa.yin(trecho, fmin=60, fmax=400, sr=sr)
        feats.append(np.concatenate([mf.mean(1), mf.std(1), [np.median(f0)]]))
    X = np.array(feats)
    X = (X - X.mean(0)) / (X.std(0) + 1e-9)
    rot = KMeans(n_clusters=2, n_init=10, random_state=0).fit_predict(X)
    for s, r in zip(segs, rot):
        s["falante"] = int(r)


def traduzir(segs, origem):
    import argostranslate.package as pk, argostranslate.translate as tr
    pk.update_package_index()
    disp = pk.get_available_packages()

    def instalar(a, b):
        p = next((p for p in disp if p.from_code == a and p.to_code == b), None)
        if p and not any(i.from_code == a and i.to_code == b for i in pk.get_installed_packages()):
            pk.install_from_path(p.download())
        return p is not None

    if origem != "pt" and not instalar(origem, "pt"):
        instalar(origem, "en"); instalar("en", "pt")  # pivô via inglês
    for s in segs:
        s["pt"] = tr.translate(s["texto"], origem, "pt") if origem != "pt" else s["texto"]


async def sintetizar(segs, pasta):
    import edge_tts
    for i, s in enumerate(segs):
        mp3 = pasta / f"{i:05d}.mp3"
        if not mp3.exists():
            await edge_tts.Communicate(s["pt"], VOZES[s["falante"]]).save(str(mp3))
        s["mp3"] = str(mp3)


def duracao(arq):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", arq], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def montar(video, segs, pasta, saida):
    """Gera a faixa dublada em blocos para não estourar a linha de comando."""
    total = duracao(str(video))
    trilha = pasta / "dublagem.wav"
    sh("ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(total), str(trilha))
    for b in range(0, len(segs), 40):
        bloco = segs[b:b + 40]
        entradas, filtros = ["-i", str(trilha)], []
        for j, s in enumerate(bloco, 1):
            janela = max(s["fim"] - s["ini"], 0.5)
            vel = min(max(duracao(s["mp3"]) / janela, 1.0), 1.6)
            entradas += ["-i", s["mp3"]]
            atraso = int(s["ini"] * 1000)
            filtros.append(f"[{j}]atempo={vel:.3f},aresample=24000,adelay={atraso}[a{j}]")
        mix = "".join(f"[a{j}]" for j in range(1, len(bloco) + 1))
        filtros.append(f"[0]{mix}amix=inputs={len(bloco)+1}:normalize=0:duration=first[o]")
        tmp = pasta / "tmp.wav"
        sh("ffmpeg", "-y", *entradas, "-filter_complex", ";".join(filtros), "-map", "[o]", str(tmp))
        tmp.replace(trilha)
    # áudio original bem baixo ao fundo + dublagem por cima
    sh("ffmpeg", "-y", "-i", str(video), "-i", str(trilha), "-filter_complex",
       "[0:a]volume=0.12[bg];[bg][1:a]amix=inputs=2:normalize=0:duration=first[a]",
       "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", str(saida / "video_dublado.mp4"))
    sh("ffmpeg", "-y", "-i", str(trilha), "-c:a", "libmp3lame", "-q:a", "4", str(saida / "audio_dublado.mp3"))


def srt(segs, arq, campo):
    def t(x):
        h, r = divmod(x, 3600); m, s = divmod(r, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"
    with open(arq, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{t(s['ini'])} --> {t(s['fim'])}\n[Voz {s['falante']+1}] {s[campo]}\n\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--saida", default="saida")
    ap.add_argument("--modelo", default="small")
    ap.add_argument("--idioma", default=None)
    a = ap.parse_args()
    video, saida = Path(a.video), Path(a.saida)
    pasta = saida / "trabalho"; pasta.mkdir(parents=True, exist_ok=True)
    audio = pasta / "audio16k.wav"
    sh("ffmpeg", "-y", "-i", str(video), "-ac", "1", "-ar", "16000", str(audio))
    cache = pasta / "segmentos.json"
    if cache.exists():
        segs, lang = json.loads(cache.read_text())
    else:
        segs, lang = transcrever(audio, a.modelo, a.idioma)
        separar_falantes(audio, segs)
        traduzir(segs, lang)
        cache.write_text(json.dumps([segs, lang], ensure_ascii=False, indent=1))
    srt(segs, saida / "legendas_original.srt", "texto")
    srt(segs, saida / "legendas_pt.srt", "pt")
    asyncio.run(sintetizar(segs, pasta))
    montar(video, segs, pasta, saida)
    print("Pronto:", saida.resolve())


if __name__ == "__main__":
    main()
