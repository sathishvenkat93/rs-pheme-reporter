import { tableModel, type ChartInput } from "./options";

export function TableView({ input, max = 200 }: { input: ChartInput; max?: number }) {
  const { header, cells } = tableModel(input);
  if (!cells.length) return <p className="p-4 text-sm text-ink-muted">No data</p>;
  return (
    <div className="h-full overflow-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">{input.widget.title}</caption>
        <thead className="sticky top-0 bg-surface text-ink-muted">
          <tr>
            {header.map((h) => (
              <th key={h} scope="col" className="border-b border-border px-2 py-1 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cells.slice(0, max).map((row, i) => (
            <tr key={i} className="odd:bg-surface-muted/40">
              {row.map((c, j) => (
                <td key={j} className="px-2 py-1 tabular-nums">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
