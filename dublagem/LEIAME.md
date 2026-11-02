# Dublagem gratuita em PT-BR (duas vozes)

Script `dublar.py`: pega um vídeo, transcreve, separa em 2 falantes, traduz para
português e dubla com uma voz masculina (Antonio) e uma feminina (Francisca).
Só usa ferramentas gratuitas:

| Etapa | Ferramenta | Custo |
|---|---|---|
| Download | yt-dlp | grátis |
| Transcrição | faster-whisper (roda no computador) | grátis |
| 2 falantes | MFCC + KMeans (librosa/scikit-learn) | grátis |
| Tradução | Argos Translate (offline) | grátis |
| Vozes | edge-tts (vozes neurais da Microsoft, sem chave) | grátis |
| Montagem | ffmpeg | grátis |

## Como usar no seu computador

```bash
pip install -r requirements.txt          # precisa também do ffmpeg instalado
pip install yt-dlp
yt-dlp -f "bv*[height<=720]+ba/b" -o video.mp4 "https://www.youtube.com/watch?v=z9gTnoftoRU"
python3 dublar.py video.mp4 --saida saida
```

Saída em `saida/`:
- `video_dublado.mp4` — vídeo com a dublagem por cima (original bem baixo ao fundo)
- `audio_dublado.mp3` — só a dublagem
- `legendas_pt.srt` e `legendas_original.srt` — com marcação [Voz 1]/[Voz 2]

Dicas:
- `--modelo medium` transcreve melhor (mais lento); `small` é o padrão.
- `--idioma en` força o idioma de origem se a detecção errar.
- Para trocar vozes, edite `VOZES` no topo do script (`edge-tts --list-voices | grep pt-BR`).
- Se um vídeo tiver mais de 2 pessoas, as vozes ficam agrupadas em 2.
