# Tech Stack — NGL Accounting System

## Web App (Frontend)

| Tool | Version | Notes |
|---|---|---|
| HTML/CSS/JS | Vanilla | No framework, no build step |
| Tailwind CSS | CDN (latest) | Utility-first styling |
| pdf-lib | 1.17.1 | Client-side PDF merge engine |
| SheetJS (xlsx) | 0.20.3 | Excel (.xlsx) parsing |
| SortableJS | 1.15.2 | Drag-and-drop reordering |
| JSZip | 3.10.1 | ZIP file creation for bulk downloads |

## Agent Server (Backend)

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12+ | Via `py` launcher |
| FastAPI | latest | REST API + SSE streaming |
| Uvicorn | latest | ASGI server |
| Playwright | latest | Browser automation (Chrome) |
| Anthropic SDK | latest | Claude Haiku for document classification |
| python-dotenv | latest | Environment variable management |

## External Services
| Service | Purpose |
|---|---|
| QuickBooks Online (QBO) | Invoice sending via browser automation |
| NGL TMS Portal | POD document retrieval |
| Gmail SMTP | OEC flow POD emails |
| TranzAct Portal | Portal upload flow |
| Claude Haiku API | AI document classification |

## Running the App
```
# Web app — open directly in browser
app/index.html

# Agent server — first-time setup
agent/setup.bat

# Agent server — start
Start Agent.bat  (or: cd agent && python main.py)
```

## Deployment
- Local-only utility (not deployed to any hosting)
- Web app runs via `file://` protocol
- Agent server runs on `localhost:8787`
