# Dublagem gratuita em PT-BR (duas vozes)

## Windows: dois cliques
1. Baixe e extraia esta pasta.
2. Dê dois cliques em **DUBLAR.bat**.
3. Na primeira vez ele instala tudo sozinho (Python e ferramentas, grátis). Depois
   pede o link do YouTube: cole ou só aperte Enter para o vídeo padrão.
4. No fim, abre a pasta `saida` com:
   - `video_dublado.mp4`: o vídeo com a dublagem em português (original bem baixo ao fundo)
   - `audio_dublado.mp3`: só a dublagem
   - `roteiro_pt.txt`: o texto traduzido, separado por VOZ 1 / VOZ 2
   - `legendas_pt.srt` e `legendas_original.srt`

Se o Windows mostrar "O Windows protegeu o computador", clique em
**Mais informações → Executar assim mesmo**.

Mac/Linux: `sh dublar.sh "LINK"`.

## O que é usado (tudo gratuito)
| Etapa | Ferramenta |
|---|---|
| Download | yt-dlp |
| Transcrição | faster-whisper (roda no próprio computador) |
| 2 falantes | timbre/tom da voz + KMeans (librosa, scikit-learn) |
| Tradução | Google Tradutor gratuito (reserva: MyMemory) |
| Vozes | edge-tts: Antonio (Voz 1, masculina) e Francisca (Voz 2, feminina) |
| Montagem | ffmpeg embutido (imageio-ffmpeg) |

## Ajustes
- Transcrição mais precisa (mais lenta): `python dublar.py LINK --modelo medium`
- Trocar vozes: edite `VOZES` no topo do `dublar.py`.
- Se parar no meio, rode de novo: o que já foi feito fica guardado em `saida/trabalho`.
  Para recomeçar do zero, apague a pasta `saida`.
