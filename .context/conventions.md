# Conventions — NGL Accounting System

## JavaScript (Web App)

### File Organization
- ES modules with explicit imports/exports
- Shared utilities in `app/assets/js/shared/`
- Tool-specific code in `app/assets/js/tools/{tool-name}/`
- Single entry point: `app.js` (loaded as `type="module"`)

### Naming
- Files: kebab-case — `agent-client.js`, `invoice-sender.js`
- Functions: camelCase — `invAddLog()`, `custLoadCustomers()`
- Constants: UPPER_SNAKE_CASE — `LS_CUSTOMERS`, `LS_SEND_HISTORY`
- DOM IDs: camelCase — `mergeToolView`, `invSendFilterWrap`

### State
- Global state objects in `shared/state.js`
- localStorage keys centralized in `shared/constants.js`

### Styling
- Tailwind utility classes via CDN
- Custom CSS in `assets/css/styles.css`
- Modal visibility via `.open` CSS class toggle

### Error Handling
- Wrap async operations in try/catch
- Log errors to Status Log UI — never silently swallow
- Skipped/failed files go to "Failure Report" section

## Python (Agent Server)

### File Organization
- Large service files split into packages using mixin pattern
- Each mixin file has its own imports (only what it uses)
- `__init__.py` combines mixins and re-exports the main class

### Naming
- Files: snake_case — `qbo_api.py`, `job_manager.py`
- Classes: PascalCase — `QBOApiClient`, `TMSBrowser`
- Private methods: `_prefixed` — `_debug()`, `_ensure_browser()`
- Logger per module: `logging.getLogger("ngl.{module}")`

### Config
- All settings in `config.py`
- Secrets in `.env` (loaded via python-dotenv)
- DOM selectors in `tms_selectors.json`

### Error Handling
- Browser automation methods return structured result objects
- Timeouts on all browser waits (never hang indefinitely)
- Debug screenshots + HTML snapshots saved to `agent/debug/`
