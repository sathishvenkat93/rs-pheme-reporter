// Aliases over the types `make types` generates from the backend's OpenAPI schema.
import type { components } from "./generated/openapi";

type S = components["schemas"];
export type Session = S["SessionOut"];
export type ChatMessage = S["ChatMessage"];
export type DraftPlan = S["DraftPlan"];
export type VisualOption = S["VisualOption"];
export type Decision = S["Decision"];
export type DecisionChoice = Decision["options"][number];
export type Upload = S["UploadOut"];
export type QueryResult = S["QueryResult"];
export type SemanticQuery = S["SemanticQuery"];
export type Measure = S["Measure"];
export type DashboardSpec = S["DashboardSpec"];
export type Widget = S["Widget"];
export type WidgetType = Widget["type"];
export type LayoutItem = S["LayoutItem"];
export type DashboardVersion = S["VersionOut"];
export type VersionMeta = S["VersionMeta"];
export type DashboardSummary = S["DashboardSummary"];
export type PatchOp = S["WidgetPatchOp"];
export type Threshold = NonNullable<Widget["thresholds"]>[number];
