# Bible Video Generator

Webapp local (FastAPI) para gerar vídeos de leitura bíblica para o YouTube:
upload do áudio (mp3) → transcrição com timestamps via Gemini → revisão da
legenda → renderização (HTML/CSS → frames → vídeo) → export em mp4.

## Requisitos

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/download.html) instalado e disponível no PATH
- Uma chave de API do Gemini (https://aistudio.google.com/apikey)

## Instalação

```bash
cd bible-video-app
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium     # baixa o Chromium usado para renderizar os frames

cp .env.example .env
# edite .env e coloque sua GEMINI_API_KEY
```

## Rodando

```bash
uvicorn main:app --reload
```

Acesse http://127.0.0.1:8000

## Fluxo de uso

1. Na tela inicial, crie um job: título/referência, formato (paisagem ou
   vertical), template visual e o mp3 da leitura.
2. Na página do job, clique em "Transcrever áudio" — o Gemini gera a
   legenda (um versículo por entrada, com tempos de início/fim).
3. Revise o texto e os tempos de cada versículo na tabela. Salve as
   alterações quando terminar de ajustar.
4. Clique em "Renderizar vídeo". O app gera um frame PNG para cada troca de
   destaque de versículo (não a cada frame de vídeo — muito mais rápido) e
   usa o ffmpeg para juntar essas imagens com o áudio original em um mp4.
5. Baixe o vídeo pronto pela própria página do job.

## Templates visuais

Cada pasta em `templates/video/<nome>/` contém um `template.html` (Jinja2)
e um `style.css` próprios. O app já vem com dois:

- **manuscrito** — fundo tinta-azul-escura, tipografia serifada, destaque
  em dourado com régua lateral (clima editorial/manuscrito).
- **painel** — fundo claro, tipografia sans-serif, destaque com selo
  circular colorido no número do versículo (clima moderno/limpo).

Para criar um novo template, duplique uma dessas pastas e ajuste o HTML/CSS.
O template recebe as variáveis: `chapter_title`, `verses` (lista de
`{verse, text}`), `current_verse` (número do versículo em destaque ou
`None`) e `video_format` (`landscape` ou `vertical`, aplicado como classe
no `<body>`).

## Estrutura

```
main.py                 # rotas FastAPI
core/
  config.py              # caminhos e configuração (.env)
  jobs.py                 # armazenamento de jobs (JSON em disco)
  srt_utils.py             # conversão segmentos <-> .srt
services/
  transcription.py        # chamada ao Gemini, gera .srt
  renderer.py              # Playwright: template -> frames PNG
  video_export.py          # ffmpeg: frames + áudio -> mp4
templates/
  app/                     # páginas do próprio webapp
  video/<template>/        # templates HTML/CSS do vídeo final
static/                   # css/js do webapp
uploads/<job_id>/          # mp3 enviados
jobs/<job_id>/              # job.json, captions.srt, frames/, concat.txt
output/<job_id>.mp4          # vídeo final
```

## Observações

- A transcrição é feita puramente pelo Gemini a partir do áudio (sem
  comparação com um texto bíblico de referência) — por isso a etapa de
  revisão antes de renderizar é importante.
- O destaque do versículo atual permanece na tela até o início da fala do
  próximo versículo (estilo teleprômpter), não apenas durante a fala dele.
- Cada job fica salvo em disco (`jobs/<id>/job.json`), então dá pra fechar o
  navegador e voltar depois — o estado não se perde.
