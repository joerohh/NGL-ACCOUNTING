# Architecture — NGL Accounting System

## Hosting & Deployment
- Target: GitHub Pages (static export)
- Framework: Next.js 15 (App Router)

## Core Constraint
**100% client-side processing.** No files are ever uploaded to a server. All PDF and Excel operations happen entirely in the browser.

## Application Layers
- **UI Layer:** React Server Components by default. Client Components only where browser APIs are required (file input, drag-and-drop, PDF rendering).
- **Logic Layer:** All PDF and Excel processing lives in `/src/utils`. Components stay clean — no business logic inside them.
- **Type Layer:** All shared TypeScript types live in `src/types/index.ts`.

## Auth
None — this is a local-first utility with no user accounts or login.

## Data Flow (Bulk Mode)
```
.xlsx file → xlsx parser (SheetJS) → extract Container # + Filename
→ match against local PDF files → pdf-lib merge → download [Container_Number].pdf
```

## Data Flow (Manual Mode)
```
User uploads 2+ PDFs → drag-and-drop reorder → pdf-lib merge → instant download
```

## State Management
React Context only. No Redux or external state libraries.

## Key Boundaries
- UI components must NOT contain PDF or Excel logic.
- All merge logic goes in `/src/utils`.
- Never introduce server-side file handling.
