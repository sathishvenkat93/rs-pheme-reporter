import type { RouteObject } from "react-router";
import { LibraryPage } from "../features/library/LibraryPage";
import { AppLayout } from "./AppLayout";
import { DashboardPage } from "./DashboardPage";
import { NewSession } from "./NewSession";
import { RouteError } from "./RouteError";
import { Workspace } from "./Workspace";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppLayout />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <NewSession /> },
      { path: "s/:sid", element: <Workspace /> },
      { path: "d/:id", element: <DashboardPage /> },
      { path: "d/:id/v/:n", element: <DashboardPage /> },
      { path: "library", element: <LibraryPage /> },
    ],
  },
];
