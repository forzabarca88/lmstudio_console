# LM Studio Console

Remote web management dashboard and chat interface for LM Studio.

## Features

- **Model Management**: List, load, and unload LM Studio models
- **Chat Interface**: Full-featured chat with any OpenAI-compatible endpoint
- **Streaming**: Real-time SSE streaming responses
- **Markdown**: Messages rendered with full Markdown support
- **Settings**: Configurable system prompt and temperature
- **Persistence**: Settings saved to localStorage between sessions

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) for package management
- LM Studio running with API server enabled

## Usage

```bash
# Start the server (default: http://localhost:8080)
uv run python backend/server.py

# Custom port and LM Studio URL
LM_CONSOLE_PORT=9090 LM_STUDIO_URL=http://localhost:1234 uv run python backend/server.py
```

Open `http://localhost:8080` in your browser.

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `LM_CONSOLE_HOST` | `0.0.0.0` | Server bind address |
| `LM_CONSOLE_PORT` | `8080` | Server port |
| `LM_STUDIO_URL` | `http://localhost:1234` | LM Studio API URL |

## Project Structure

```
├── backend/
│   └── server.py      # HTTP server + API proxy
├── static/
│   ├── css/
│   │   └── style.css  # Styles
│   ├── js/
│   │   └── app.js     # Application logic
│   └── index.html     # Page structure
├── pyproject.toml
└── README.md
```
