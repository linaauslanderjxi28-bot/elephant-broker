/**
 * supertokens.ts — SuperTokens React SDK initialisation (side-effect module).
 *
 * Imported once for its side effect from `App.tsx` (`import "./supertokens"`)
 * before the Refine tree mounts. Calling `SuperTokens.init(...)` here:
 *  - registers the EmailPassword + Session recipes the dashboard relies on
 *    (login/register/forgot-password + cookie/CSRF/refresh lifecycle), and
 *  - patches the global `fetch` (via Session.init) so `apiClient`'s plain
 *    `fetch` transparently gains the session cookie, anti-CSRF header, and
 *    automatic access-token refresh.
 *
 * `appInfo` is sourced from Vite env (`.env.example`) so the same build works
 * cross-origin in dev (dashboard at :5173, runtime at :8420) and same-origin in
 * production (runtime serves the bundle at `/ui/*`, so apiDomain is empty). The
 * nullish `??` fallback chain preserves an explicit empty string (same-origin)
 * and only substitutes the dev default when the var is genuinely undefined.
 */

import SuperTokens from "supertokens-auth-react";
import Session from "supertokens-auth-react/recipe/session";
import EmailPassword from "supertokens-auth-react/recipe/emailpassword";

const env = (import.meta as any).env ?? {};

/** apiDomain — where the SuperTokens `/auth/*` routes live (the EB runtime). */
const apiDomain: string =
  env.VITE_ST_API_DOMAIN ?? env.VITE_EB_RUNTIME_URL ?? "http://localhost:8420";

/** websiteDomain — where this dashboard is served. */
const websiteDomain: string =
  env.VITE_ST_WEBSITE_DOMAIN ?? env.VITE_DASHBOARD_URL ?? "http://localhost:5173";

/** apiBasePath — SuperTokens route prefix on the runtime (defaults to /auth). */
const apiBasePath: string = env.VITE_ST_API_BASE_PATH ?? "/auth";

/** appName — display name shown in SuperTokens-managed emails/UI. */
const appName: string = env.VITE_ST_APP_NAME ?? "ElephantBroker Dashboard";

SuperTokens.init({
  appInfo: {
    appName,
    apiDomain,
    websiteDomain,
    apiBasePath,
  },
  recipeList: [EmailPassword.init(), Session.init()],
});

export {};
