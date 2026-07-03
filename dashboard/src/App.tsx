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
 *   ./pages/consolidation/list          — default ConsolidationPage
 *   ./pages/trace/list                  — default TraceListPage
 *   ./pages/settings/api-keys|preferences|authority|config|indexes — default page components
 *   ./pages/auth/login|register|forgot-password            — default page components
 */
import {
  Authenticated,
  CanAccess,
  Refine,
  useGetIdentity,
  type ResourceProps,
} from "@refinedev/core";
import {
  Breadcrumb,
  ErrorComponent,
  HamburgerMenu,
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

import AppBar from "@mui/material/AppBar";
import Avatar from "@mui/material/Avatar";
import CssBaseline from "@mui/material/CssBaseline";
import GlobalStyles from "@mui/material/GlobalStyles";
import Stack from "@mui/material/Stack";
import Toolbar from "@mui/material/Toolbar";
import Typography from "@mui/material/Typography";
import { ThemeProvider } from "@mui/material/styles";

// Section icons (MUI)
import DashboardIcon from "@mui/icons-material/Dashboard";
import StorageIcon from "@mui/icons-material/Storage";
import TableRowsIcon from "@mui/icons-material/TableRows";
import SearchIcon from "@mui/icons-material/Search";
import BarChartIcon from "@mui/icons-material/BarChart";
import BubbleChartIcon from "@mui/icons-material/BubbleChart";
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
import SpeedIcon from "@mui/icons-material/Speed";
import BedtimeIcon from "@mui/icons-material/Bedtime";
import ManageSearchIcon from "@mui/icons-material/ManageSearch";

import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Link as RouterLink,
  Outlet,
  Route,
  Routes,
} from "react-router-dom";
import { SuperTokensWrapper } from "supertokens-auth-react";

// Side-effect: initialize the SuperTokens React SDK (owned by providers agent).
import "./supertokens";

import { authProvider } from "./providers/authProvider";
import { dataProvider } from "./providers/dataProvider";
import {
  accessControlProvider,
  invalidateAuthorityCache,
} from "./accessControlProvider";
import { ebTheme, ebThemeDark } from "./theme";
import GatewaySelector from "./components/GatewaySelector";
import BrandLogo from "./components/BrandLogo";
import ErrorBoundary from "./components/ErrorBoundary";
import { humanizeEnum } from "./lib/format";

// --- Pages (owned by pages agents; referenced by default import) ---
import OverviewPage from "./pages/home";
import MemoryList from "./pages/memory/list";
import MemoryShow from "./pages/memory/show";
import MemorySearch from "./pages/memory/search";
import MemoryStats from "./pages/memory/stats";
import MemoryGraph from "./pages/memory/graph";
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
import ConsolidationPage from "./pages/consolidation/list";
import TraceExplorerPage from "./pages/trace/list";
import ApiKeysPage from "./pages/settings/api-keys";
import PreferencesPage, {
  PREF_KEYS,
  PREFS_CHANGED_EVENT,
} from "./pages/settings/preferences";
import AuthorityRulesPage from "./pages/settings/authority";
import EffectiveConfigPage from "./pages/settings/config";
import FactIndexesPage from "./pages/settings/indexes";
import LoginPage from "./pages/auth/login";
import RegisterPage from "./pages/auth/register";
import ForgotPasswordPage from "./pages/auth/forgot-password";
import ResetPasswordPage from "./pages/auth/reset-password";

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
  {
    name: "memory-graph",
    list: "/memory/graph",
    meta: { parent: "memory_section", label: "Graph", icon: <BubbleChartIcon /> },
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
  {
    name: "consolidation",
    list: "/consolidation",
    meta: {
      parent: "runtime_section",
      label: "Consolidation",
      icon: <BedtimeIcon />,
    },
  },
  {
    name: "trace",
    list: "/trace",
    meta: {
      parent: "runtime_section",
      label: "Trace Explorer",
      icon: <ManageSearchIcon />,
    },
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
  {
    name: "fact-indexes",
    list: "/settings/indexes",
    meta: {
      parent: "settings_section",
      label: "Fact Indexes",
      icon: <SpeedIcon />,
    },
  },
];

// BrowserRouter basename derived from Vite's base ("/ui/" in prod, "/" in dev).
const basename = import.meta.env.BASE_URL.replace(/\/$/, "") || "/";

/**
 * Layout header — mirrors Refine's default ThemedHeaderV2 (sticky AppBar,
 * hamburger toggle, identity block) with the GatewaySelector mounted in the
 * middle. The selector persists via the unified gateway-key helpers
 * (providers/gatewayKey.ts, through apiClient's get/setSelectedGateway) and
 * broadcasts GATEWAY_CHANGED_EVENT; we additionally drop the cached authority
 * level on switch so access-control gates re-resolve for the new scope.
 */
/**
 * Sidebar brand slot (Refine ThemedLayoutV2 `Title` contract) — the
 * elephant.broker lockup from the EB Logo - Monogram design; seal-only when
 * the sider is collapsed. Links home like Refine's default title.
 */
const AppTitle = ({ collapsed }: { collapsed: boolean }) => (
  <RouterLink to="/" style={{ textDecoration: "none", display: "flex" }}>
    <BrandLogo size={32} sealOnly={collapsed} />
  </RouterLink>
);

const AppHeader = () => {
  const { data: user } = useGetIdentity<{ name?: string; avatar?: string }>();

  return (
    <AppBar position="sticky">
      <Toolbar>
        <HamburgerMenu />
        <Stack
          direction="row"
          width="100%"
          justifyContent="flex-end"
          alignItems="center"
          gap="16px"
        >
          <GatewaySelector onChange={() => invalidateAuthorityCache()} />
          <Stack
            direction="row"
            gap="16px"
            alignItems="center"
            justifyContent="center"
          >
            {user?.name && (
              <Typography variant="subtitle2" data-testid="header-user-name">
                {user.name}
              </Typography>
            )}
            {user?.avatar && <Avatar src={user.avatar} alt={user.name} />}
          </Stack>
        </Stack>
      </Toolbar>
    </AppBar>
  );
};

/** Read the persisted "Theme" preference (settings-3). Defaults to light. */
function readThemeMode(): "light" | "dark" {
  if (typeof window === "undefined" || !window.localStorage) return "light";
  try {
    return window.localStorage.getItem(PREF_KEYS.theme) === "dark"
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

function App() {
  // The Preferences page writes `PREF_KEYS.theme` and broadcasts
  // PREFS_CHANGED_EVENT on save (and on initial load). We mirror that into React
  // state so the MUI ThemeProvider swaps light/dark immediately — no reload —
  // and stays in sync across tabs via the native `storage` event (settings-3).
  const [themeMode, setThemeMode] = useState<"light" | "dark">(readThemeMode);
  useEffect(() => {
    const sync = () => setThemeMode(readThemeMode());
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === PREF_KEYS.theme) sync();
    };
    window.addEventListener(PREFS_CHANGED_EVENT, sync);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(PREFS_CHANGED_EVENT, sync);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return (
    <BrowserRouter basename={basename}>
      <SuperTokensWrapper>
        <ThemeProvider theme={themeMode === "dark" ? ebThemeDark : ebTheme}>
          <CssBaseline />
          <GlobalStyles styles={{ html: { WebkitFontSmoothing: "auto" } }} />
          <RefineSnackbarProvider>
            <Refine
              routerProvider={routerBindings}
              dataProvider={dataProvider}
              authProvider={authProvider}
              accessControlProvider={accessControlProvider}
              notificationProvider={useNotificationProvider}
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
                      <ThemedLayoutV2 Header={AppHeader} Title={AppTitle}>
                        {/* One consistent breadcrumb trail for every page
                            (cross-cutting-6). Refine derives it from the active
                            resource hierarchy and self-hides on the root
                            overview (single segment). Rendering it once here in
                            the layout — instead of relying on the single, broken
                            per-page <List> breadcrumb — keeps the trail correct
                            and uniform. The sx restores breathing room between a
                            section's icon and its label (the default cramps
                            non-linked parent segments). */}
                        <Breadcrumb
                          breadcrumbProps={{
                            sx: {
                              mb: 2,
                              "& .MuiBreadcrumbs-li .MuiSvgIcon-root": {
                                mr: 0.5,
                              },
                            },
                          }}
                        />
                        {/* Boundary scopes a render crash to the current view
                            (keeps sidebar/header alive) instead of white-screening
                            the whole app — defense-in-depth for RC-3. */}
                        <ErrorBoundary>
                          <Outlet />
                        </ErrorBoundary>
                      </ThemedLayoutV2>
                    </Authenticated>
                  }
                >
                  <Route index element={<OverviewPage />} />

                  <Route path="memory">
                    <Route index element={<MemoryList />} />
                    <Route path="search" element={<MemorySearch />} />
                    <Route path="stats" element={<MemoryStats />} />
                    <Route path="graph" element={<MemoryGraph />} />
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
                  <Route
                    path="consolidation"
                    element={<ConsolidationPage />}
                  />
                  <Route
                    path="trace"
                    element={
                      // Route-level gate expected by pages/trace/list.tsx (its
                      // in-page authority check is the courtesy fallback).
                      <CanAccess
                        resource="trace"
                        action="list"
                        fallback={<ErrorComponent />}
                      >
                        <TraceExplorerPage />
                      </CanAccess>
                    }
                  />

                  <Route path="settings">
                    <Route path="api-keys" element={<ApiKeysPage />} />
                    <Route path="preferences" element={<PreferencesPage />} />
                    <Route path="authority" element={<AuthorityRulesPage />} />
                    <Route path="config" element={<EffectiveConfigPage />} />
                    <Route path="indexes" element={<FactIndexesPage />} />
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
                  {/* Landing page for the emailed reset link (fixes auth-2). */}
                  <Route
                    path="/reset-password"
                    element={<ResetPasswordPage />}
                  />
                </Route>
              </Routes>

              <UnsavedChangesNotifier />
              {/* Force a consistent "<Resource> | ElephantBroker" document
                  title on every page (the default handler otherwise leaks
                  "Refine" — cross-cutting-5). */}
              <DocumentTitleHandler
                handler={({ resource, action, params }) => {
                  const appName = "ElephantBroker";
                  const label =
                    (resource?.meta?.label as string | undefined) ??
                    (resource?.name ? humanizeEnum(resource.name) : undefined);
                  if (!label) return appName;

                  let prefix = label;
                  if (action === "show" && params?.id) {
                    prefix = `${label} #${params.id}`;
                  } else if (action === "edit" && params?.id) {
                    prefix = `Edit ${label} #${params.id}`;
                  } else if (action === "create") {
                    prefix = `Create ${label}`;
                  }
                  return `${prefix} | ${appName}`;
                }}
              />
            </Refine>
          </RefineSnackbarProvider>
        </ThemeProvider>
      </SuperTokensWrapper>
    </BrowserRouter>
  );
}

export default App;
