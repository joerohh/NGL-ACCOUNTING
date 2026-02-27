# Tech Stack — NGL Accounting System

> Update exact versions after running `npm install`.

## Framework & Language
| Tool | Version | Notes |
|---|---|---|
| Next.js | 15.x | App Router, static export for GitHub Pages |
| TypeScript | 5.x | Strict mode enabled |
| React | 19.x | Server Components default |

## Styling
| Tool | Version | Notes |
|---|---|---|
| Tailwind CSS | 4.x | Utility-first |
| Shadcn/UI | latest | Component library, Industrial/Clean theme |

## Core Libraries
| Tool | Version | Notes |
|---|---|---|
| pdf-lib | latest | Client-side PDF merge engine |
| xlsx (SheetJS) | latest | Excel (.xlsx) parsing |
| lucide-react | latest | Icon set |

## Development
| Tool | Version | Notes |
|---|---|---|
| ESLint | latest | `npm run lint` |
| Node.js | 20.x LTS | Recommended runtime |

## Commands
```bash
npm run dev      # Start development server
npm run build    # Production build (static export)
npm run lint     # Run ESLint
```

## Deployment Target
GitHub Pages — static HTML/CSS/JS export, no server runtime needed.
