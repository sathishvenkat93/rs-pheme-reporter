import type { RouteObject } from "react-router";
import { AppLayout } from "./AppLayout";
import { Workspace } from "./Workspace";
import { LibraryPage } from "../features/library/LibraryPage";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <Workspace /> },
      { path: "library", element: <LibraryPage /> },
    ],
  },
];
