"""Auto-layout on the 12-column grid.

KPI tiles are 3 wide across the top; trends are full width (half width in pairs when there
are several, or when the plan asks for two columns); breakdowns and distributions are half
width; tables are full width at the bottom.
"""

from __future__ import annotations

from app.models.spec import GRID_COLUMNS, LayoutItem, Widget, WidgetType

TRENDS = frozenset({WidgetType.LINE, WidgetType.AREA})
TABLES = frozenset({WidgetType.TABLE})
KPI_W, KPI_H = 3, 2
HALF, FULL = 6, 12
CHART_H, TABLE_H = 4, 5


def auto_layout(widgets: list[Widget], hints: list[str] | None = None) -> list[LayoutItem]:
    hints = hints or []
    kpis = [w for w in widgets if w.type == WidgetType.KPI]
    trends = [w for w in widgets if w.type in TRENDS]
    tables = [w for w in widgets if w.type in TABLES]
    rest = [w for w in widgets if w not in kpis and w not in trends and w not in tables]
    items: list[LayoutItem] = []
    y = 0
    for i, w in enumerate(kpis):
        x = (i * KPI_W) % GRID_COLUMNS
        if i and x == 0:
            y += KPI_H
        items.append(LayoutItem(widget=w.id, x=x, y=y, w=KPI_W, h=KPI_H))
    if kpis:
        y += KPI_H
    trend_w = HALF if len(trends) > 1 or "two_columns" in hints else FULL
    y = _flow(items, trends, y, trend_w)
    y = _flow(items, rest, y, HALF)
    for w in tables:
        items.append(LayoutItem(widget=w.id, x=0, y=y, w=FULL, h=TABLE_H))
        y += TABLE_H
    return items


def _flow(items: list[LayoutItem], widgets: list[Widget], y: int, width: int) -> int:
    x = 0
    for w in widgets:
        if x + width > GRID_COLUMNS:
            x, y = 0, y + CHART_H
        items.append(LayoutItem(widget=w.id, x=x, y=y, w=width, h=CHART_H))
        x += width
    return y + CHART_H if widgets else y
