/**
 * ElephantBroker dashboard root.
 *
 * Wires Refine (router / data / auth / access-control providers) to the ~20
 * dashboard pages defined in the Phase 11 plan (§11.3.5). The provider modules
 * and page modules are owned by sibling agents and referenced here by import —
 * this file is the integration surface that stitches them together.
 *
 * Import contract (must be provided by the pages / providers agents). Pages use
 * per-file `export default` (matching the SOW file names); providers use named
 * exports:
 *   ./supertokens                       — side-effect: SuperTokens.init(...)
 *   ./providers/authProvider            — { authProvider }
 *   ./providers/dataProvider            — { dataProvider }
 *   ./pages/home                        — default OverviewPage
 *   ./pages/memory/list|show|search|stats — default page components
 *   ./pages/goals/list                  — default GoalsPage
 *   ./pages/procedures/list             — default ProceduresPage
 *   ./pages/actors/list|show            — default page components
 *   ./pages/organizations/list|show     — default page components
 *   ./pages/sessions/list|show          — default page components
 *   ./pages/guards/list                 — default GuardsPage
 *   ./pages/profiles/list               — default ProfilesPage
 *   ./pages/settings/api-keys|preferences|authority|config — default page components
 *   ./pages/auth/login|register|forgot-password            — default page components
 */
import { Authenticated, Refine, type ResourceProps } from "@refinedev/core";
import {
  ErrorComponent,
  RefineSnackbarProvider,
  ThemedLayoutV2,
  useNotificationProvider,
} from "@refinedev/mui";
import routerBindings, {
  CatchAllNavigate,
  DocumentTitleHandler,
  NavigateToResource,
  UnsavedChangesNotifier,
} from "@refinedev/react-router-v6";

import CssBaseline from "@mui/material/CssBaseline";
import GlobalStyles from "@mui/material/GlobalStyles";
import { ThemeProvider } from "@mui/material/styles";

// Section icons (MUI)
import DashboardIcon from "@mui/icons-material/Dashboard";
import StorageIcon from "@mui/icons-material/Storage";
import TableRowsIcon from "@mui/icons-material/TableRows";
import SearchIcon from "@mui/icons-material/Search";
import BarChartIcon from "@mui/icons-material/BarChart";
import HubIcon from "@mui/icons-material/Hub";
import FlagIcon from "@mui/icons-material/Flag";
import PlaylistPlayIcon from "@mui/icons-material/PlaylistPlay";
import GroupsIcon from "@mui/icons-material/Groups";
import PersonIcon from "@mui/icons-material/Person";
import ApartmentIcon from "@mui/icons-material/Apartment";
import BoltIcon from "@mui/icons-material/Bolt";
import TerminalIcon from "@mui/icons-material/Terminal";
import ShieldIcon from "@mui/icons-material/Shield";
import TuneIcon from "@mui/icons-material/Tune";
import SettingsIcon from "@mui/icons-material/Settings";
import VpnKeyIcon from "@mui/icons-material/VpnKey";
import ManageAccountsIcon from "@mui/icons-material/ManageAccounts";
import GavelIcon from "@mui/icons-material/Gavel";
import DescriptionIcon from "@mui/icons-material/Description";

import { BrowserRouter, Outlet, Route, Routes } from "react-router-dom";
import { SuperTokensWrapper } from "supertokens-auth-react";

// Side-effect: initialize the SuperTokens React SDK (owned by providers agent).
import "./supertokens";

import { authProvider } from "./providers/authProvider";
import { dataProvider } from "./providers/dataProvider";
import { accessControlProvider } from "./accessControlProvider";
import { ebTheme } from "./theme";

// --- Pages (owned by pages agents; referenced by default import) ---
import OverviewPage from "./pages/home";
import MemoryList from "./pages/memory/list";
import MemoryShow from "./pages/memory/show";
import MemorySearch from "./pages/memory/search";
import MemoryStats from "./pages/memory/stats";
import GoalsList from "./pages/goals/list";
import ProceduresList from "./pages/procedures/list";
import ActorsList from "./pages/actors/list";
import ActorShow from "./pages/actors/show";
import OrganizationsList from "./pages/organizations/list";
import OrganizationShow from "./pages/organizations/show";
import SessionsList from "./pages/sessions/list";
import SessionShow from "./pages/sessions/show";
import GuardsPage from "./pages/guards/list";
import ProfilesPage from "./pages/profiles/list";
import ApiKeysPage from "./pages/settings/api-keys";
import PreferencesPage from "./pages/settings/preferences";
import AuthorityRulesPage from "./pages/settings/authority";
import EffectiveConfigPage from "./pages/settings/config";
import LoginPage from "./pages/auth/login";
import RegisterPage from "./pages/auth/register";
import ForgotPasswordPage from "./pages/auth/forgot-password";

/**
 * Refine resource tree — six top-level sections (Phase 11 plan §11.3.5),
 * grouped via parent "section" resources so the sidebar renders collapsible
 * groups. Child resource `name` values match the data-provider RESOURCE_MAP.
 */
const resources: ResourceProps[] = [
  {
    name: "overview",
    list: "/",
    meta: { label: "Overview", icon: <DashboardIcon /> },
  },

  // --- Section: Memory ---
  {
    name: "memory_section",
    meta: { label: "Memory", icon: <StorageIcon /> },
  },
  {
    name: "memory",
    list: "/memory",
    show: "/memory/:id",
    meta: { parent: "memory_section", label: "Browse", icon: <TableRowsIcon /> },
  },
  {
    name: "memory-search",
    list: "/memory/search",
    meta: { parent: "memory_section", label: "Search", icon: <SearchIcon /> },
  },
  {
    name: "memory-stats",
    list: "/memory/stats",
    meta: { parent: "memory_section", label: "Stats", icon: <BarChartIcon /> },
  },

  // --- Section: Knowledge ---
  {
    name: "knowledge_section",
    meta: { label: "Knowledge", icon: <HubIcon /> },
  },
  {
    name: "goals",
    list: "/goals",
    meta: { parent: "knowledge_section", label: "Goals", icon: <FlagIcon /> },
  },
  {
    name: "procedures",
    list: "/procedures",
    meta: {
      parent: "knowledge_section",
      label: "Procedures",
      icon: <PlaylistPlayIcon />,
    },
  },

  // --- Section: Actors & Organizations ---
  {
    name: "org_section",
    meta: { label: "Actors & Orgs", icon: <GroupsIcon /> },
  },
  {
    name: "actors",
    list: "/actors",
    show: "/actors/:id",
    meta: { parent: "org_section", label: "Actors", icon: <PersonIcon /> },
  },
  {
    name: "organizations",
    list: "/organizations",
    show: "/organizations/:id",
    meta: {
      parent: "org_section",
      label: "Organizations",
      icon: <ApartmentIcon />,
    },
  },

  // --- Section: Runtime ---
  {
    name: "runtime_section",
    meta: { label: "Runtime", icon: <BoltIcon /> },
  },
  {
    name: "sessions",
    list: "/sessions",
    show: "/sessions/:key",
    meta: { parent: "runtime_section", label: "Sessions", icon: <TerminalIcon /> },
  },
  {
    name: "guards",
    list: "/guards",
    meta: { parent: "runtime_section", label: "Guards", icon: <ShieldIcon /> },
  },
  {
    name: "profiles",
    list: "/profiles",
    meta: { parent: "runtime_section", label: "Profiles", icon: <TuneIcon /> },
  },

  // --- Section: Settings ---
  {
    name: "settings_section",
    meta: { label: "Settings", icon: <SettingsIcon /> },
  },
  {
    name: "api-keys",
    list: "/settings/api-keys",
    meta: { parent: "settings_section", label: "API Keys", icon: <VpnKeyIcon /> },
  },
  {
    name: "preferences",
    list: "/settings/preferences",
    meta: {
      parent: "settings_section",
      label: "Preferences",
      icon: <ManageAccountsIcon />,
    },
  },
  {
    name: "authority-rules",
    list: "/settings/authority",
    meta: {
      parent: "settings_section",
      label: "Authority Rules",
      icon: <GavelIcon />,
    },
  },
  {
    name: "effective-config",
    list: "/settings/config",
    meta: {
      parent: "settings_section",
      label: "Effective Config",
      icon: <DescriptionIcon />,
    },
  },
];

// BrowserRouter basename derived from Vite's base ("/ui/" in prod, "/" in dev).
const basename = import.meta.env.BASE_URL.replace(/\/$/, "") || "/";

function App() {
  return (
    <BrowserRouter basename={basename}>
      <SuperTokensWrapper>
        <ThemeProvider theme={ebTheme}>
          <CssBaseline />
          <GlobalStyles styles={{ html: { WebkitFontSmoothing: "auto" } }} />
          <RefineSnackbarProvider>
            <Refine
              routerProvider={routerBindings}
              dataProvider={dataProvider}
              authProvider={authProvider}
              accessControlProvider={accessControlProvider}
              notificationProvider={useNotificationProvider()}
              resources={resources}
              options={{
                syncWithLocation: true,
                warnWhenUnsavedChanges: true,
                useNewQueryKeys: true,
                projectId: "elephantbroker-dashboard",
                title: { text: "ElephantBroker", icon: <DashboardIcon /> },
              }}
            >
              <Routes>
                {/* --- Authenticated application shell --- */}
                <Route
                  element={
                    <Authenticated
                      key="authenticated-app"
                      fallback={<CatchAllNavigate to="/login" />}
                    >
                      <ThemedLayoutV2>
                        <Outlet />
                      </ThemedLayoutV2>
                    </Authenticated>
                  }
                >
                  <Route index element={<OverviewPage />} />

                  <Route path="memory">
                    <Route index element={<MemoryList />} />
                    <Route path="search" element={<MemorySearch />} />
                    <Route path="stats" element={<MemoryStats />} />
                    <Route path=":id" element={<MemoryShow />} />
                  </Route>

                  <Route path="goals" element={<GoalsList />} />
                  <Route path="procedures" element={<ProceduresList />} />

                  <Route path="actors">
                    <Route index element={<ActorsList />} />
                    <Route path=":id" element={<ActorShow />} />
                  </Route>

                  <Route path="organizations">
                    <Route index element={<OrganizationsList />} />
                    <Route path=":id" element={<OrganizationShow />} />
                  </Route>

                  <Route path="sessions">
                    <Route index element={<SessionsList />} />
                    <Route path=":key" element={<SessionShow />} />
                  </Route>

                  <Route path="guards" element={<GuardsPage />} />
                  <Route path="profiles" element={<ProfilesPage />} />

                  <Route path="settings">
                    <Route path="api-keys" element={<ApiKeysPage />} />
                    <Route path="preferences" element={<PreferencesPage />} />
                    <Route path="authority" element={<AuthorityRulesPage />} />
                    <Route path="config" element={<EffectiveConfigPage />} />
                  </Route>

                  <Route path="*" element={<ErrorComponent />} />
                </Route>

                {/* --- Unauthenticated auth pages --- */}
                <Route
                  element={
                    <Authenticated
                      key="authenticated-auth"
                      fallback={<Outlet />}
                    >
                      <NavigateToResource resource="overview" />
                    </Authenticated>
                  }
                >
                  <Route path="/login" element={<LoginPage />} />
                  <Route path="/register" element={<RegisterPage />} />
                  <Route
                    path="/forgot-password"
                    element={<ForgotPasswordPage />}
                  />
                </Route>
              </Routes>

              <UnsavedChangesNotifier />
              <DocumentTitleHandler />
            </Refine>
          </RefineSnackbarProvider>
        </ThemeProvider>
      </SuperTokensWrapper>
    </BrowserRouter>
  );
}

export default App;
