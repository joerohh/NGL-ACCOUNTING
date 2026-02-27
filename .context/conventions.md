# Conventions — NGL Accounting System

## File Naming
- Components: PascalCase — `MergeDashboard.tsx`, `FileDropZone.tsx`
- Utilities: camelCase — `pdfMerger.ts`, `xlsxParser.ts`
- Types file: `src/types/index.ts`
- Routes: lowercase folders — `app/merge/page.tsx`

## TypeScript
- Strict mode is ON — no `any` types.
- Prefer `type` over `interface`.
- Use `import type` for type-only imports.
- Props defined inline with a named `type`:
  ```ts
  type FileDropZoneProps = {
    onFilesSelected: (files: File[]) => void
  }
  ```

## Components
- Functional components only — no class components.
- Server Components are the default.
- Add `"use client"` only when browser APIs are needed (file input, drag-and-drop, etc.).
- `"use client"` must be the very first line of the file.

## Styling
- Tailwind utility classes directly in JSX.
- Use the `cn()` utility for conditional class names.
- Follow Industrial/Clean aesthetic (Shadcn/UI base).

## Logic
- All PDF and Excel logic lives in `/src/utils` — never inside components.
- Keep utility functions pure and independently testable.

## Error Handling
- Wrap all async operations in `try/catch`.
- Log errors to the Status Log UI — never silently swallow them.
- Skipped/failed files go to the "Failure Report" section.

## Constants
- Named in `UPPER_SNAKE_CASE`.

## Images
- Always use Next.js `<Image>` — never `<img>`.
- Always include `alt` and either `width`/`height` or `fill`.
