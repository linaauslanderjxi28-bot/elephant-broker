# ElephantBroker Dashboard

Operator dashboard for the ElephantBroker cognitive runtime (Phase 11). A
Vite + React + [Refine](https://refine.dev) + MUI single-page app that consumes
the runtime's `/dashboard/*`, `/admin/*`, `/trace/*`, and `/auth/*` APIs.

- **Auth:** SuperTokens (email/password + session) via `supertokens-auth-react`.
- **Data grids/charts:** `@mui/x-data-grid` + `@mui/x-charts`.
- **Access control:** authority-band gating mirrored from the backend
  `require_authority(min_level)` dependency.

## Quick start

```bash
cd dashboard
cp .env.example .env.development   # adjust VITE_EB_RUNTIME_URL / SuperTokens domains
npm install
npm run dev                        # http://localhost:5173
```

The EB runtime must be running on `VITE_EB_RUNTIME_URL` (default
`http://localhost:8420`) with SuperTokens Core reachable and CORS configured for
the dashboard origin (`credentials: true`).

## Scripts

| Script            | Purpose                                             |
| ----------------- | --------------------------------------------------- |
| `npm run dev`     | Vite dev server (HMR) at `:5173`                    |
| `npm run build`   | Type-check + production build into `dist/`          |
| `npm run preview` | Serve the production build locally                  |
| `npm run test`    | Vitest (jsdom) component/provider tests             |
| `npm run lint`    | ESLint over `src`                                   |

## Production serving

`npm run build` emits static assets into `dashboard/dist/` with `base: "/ui/"`.
The EB runtime serves that directory at `/ui/*` (same-origin, no CORS) —
respecting `EB_DASHBOARD_STATIC_DIR`. In production, `.env.production` should set
`VITE_EB_RUNTIME_URL=` (empty = same-origin).

## Project layout

```
dashboard/
  index.html
  vite.config.ts            # base=/ui/ in prod, vitest (jsdom) config
  tsconfig.json
  .env.example              # copy to .env.development / .env.production
  src/
    main.tsx                # React root
    App.tsx                 # Refine wiring: providers + resources + routes
    theme.ts                # MUI theme (teal/navy palette, Inter)
    accessControlProvider.ts# authority-band access control
    supertokens.ts          # (providers agent) SuperTokens.init
    providers/
      authProvider.ts       # (providers agent)
      dataProvider.ts       # (providers agent)
    pages/                  # (pages agents) — see import contract below
    components/             # (pages agents) shared components
    __tests__/              # (tests agent) incl. setup.ts referenced by vitest
```

## Import contract (App.tsx integration surface)

`src/App.tsx` is the scaffold that stitches sibling-owned modules together.
Providers are imported by **named** export; pages by **default** export (each
page file has `export default`, matching the SOW file names):

| Module                                | Import                          |
| ------------------------------------- | ------------------------------- |
| `./supertokens`                       | side-effect (`SuperTokens.init`)|
| `./providers/authProvider`            | `{ authProvider }`              |
| `./providers/dataProvider`            | `{ dataProvider }`              |
| `./pages/home`                        | default → Overview              |
| `./pages/memory/{list,show,search,stats}` | default each                |
| `./pages/goals/list`                  | default                         |
| `./pages/procedures/list`             | default                         |
| `./pages/actors/{list,show}`          | default each                    |
| `./pages/organizations/{list,show}`   | default each                    |
| `./pages/sessions/{list,show}`        | default each                    |
| `./pages/guards/list`                 | default                         |
| `./pages/profiles/list`               | default                         |
| `./pages/settings/{api-keys,preferences,authority,config}` | default each |
| `./pages/auth/{login,register,forgot-password}` | default each          |

The Refine resource `name` values (`memory`, `actors`, `organizations`, `goals`,
`procedures`, `sessions`, `profiles`, `guards`, `api-keys`, ...) must match the
`RESOURCE_MAP` keys in `dataProvider.ts`.

## Routes (Phase 11 plan §11.3.5)

| Path                     | Page                | Section    |
| ------------------------ | ------------------- | ---------- |
| `/`                      | Overview            | Home       |
| `/memory`                | Memory Browse       | Memory     |
| `/memory/search`         | Memory Search       | Memory     |
| `/memory/stats`          | Memory Stats        | Memory     |
| `/memory/:id`            | Fact Detail         | Memory     |
| `/goals`                 | Goals               | Knowledge  |
| `/procedures`            | Procedures          | Knowledge  |
| `/actors`                | Actors              | Actors&Org |
| `/actors/:id`            | Actor Detail        | Actors&Org |
| `/organizations`         | Organizations       | Actors&Org |
| `/organizations/:id`     | Org Detail          | Actors&Org |
| `/sessions`              | Sessions            | Runtime    |
| `/sessions/:key`         | Session Detail      | Runtime    |
| `/guards`                | Guards              | Runtime    |
| `/profiles`              | Profiles            | Runtime    |
| `/settings/api-keys`     | API Keys            | Settings   |
| `/settings/preferences`  | Preferences         | Settings   |
| `/settings/authority`    | Authority Rules (≥90) | Settings |
| `/settings/config`       | Effective Config (≥70)| Settings |
| `/login` `/register` `/forgot-password` | Auth pages | (public) |

## Access control

`accessControlProvider.ts` maps `(resource, action)` to an authority-level
threshold and consults `authProvider.getPermissions()` (which reads
`/auth/identity`). Refine's `<CanAccess>` and menu use this to hide controls the
caller cannot use. Notable thresholds: fact edit/promote ≥ 50, fact delete ≥ 70,
guard rule create/edit ≥ 70, approvals tab ≥ 50, authority-rules ≥ 90, effective
config ≥ 70, GatewaySelector "all gateways" ≥ 90. The backend
`require_authority(min_level)` remains the source of truth.
