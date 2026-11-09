import { Link, isRouteErrorResponse, useRouteError } from "react-router";

export function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error) ? error.statusText : error instanceof Error ? error.message : "Unknown error";
  return (
    <div role="alert" className="mx-auto max-w-lg p-8 text-sm">
      <h1 className="text-lg font-semibold">Something went wrong</h1>
      <p className="mt-2 text-ink-muted">{message}</p>
      <Link to="/" className="mt-4 inline-block text-accent underline">Start a new session</Link>
    </div>
  );
}
