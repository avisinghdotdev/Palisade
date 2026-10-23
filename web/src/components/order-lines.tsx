import type { Money, OrderLine } from "@/api/client";

import { formatAmount } from "@/money";

/** The lines of an order and its total, as the shop and the console both print them. */
export function OrderLines({
  lines,
  total,
}: {
  lines: OrderLine[];
  total: Money;
}) {
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-muted-foreground">
        <tr className="border-b">
          <th className="py-2 pr-4 font-medium" scope="col">
            Item
          </th>
          <th className="py-2 pr-4 font-medium" scope="col">
            Size
          </th>
          <th className="py-2 pr-4 text-right font-medium" scope="col">
            Qty
          </th>
          <th className="py-2 text-right font-medium" scope="col">
            Price
          </th>
        </tr>
      </thead>
      <tbody>
        {lines.map((line) => (
          <tr className="border-b" key={`${line.product_id}/${line.size}`}>
            <th className="py-2 pr-4 text-left font-normal" scope="row">
              {line.brand} {line.name}
            </th>
            <td className="py-2 pr-4">US {line.size}</td>
            <td className="py-2 pr-4 text-right tabular-nums">
              {line.quantity}
            </td>
            <td className="py-2 text-right tabular-nums">
              {formatAmount(line.line_total.value)}
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <th className="pt-3 text-left font-semibold" colSpan={3} scope="row">
            Total
          </th>
          <td className="pt-3 text-right font-semibold tabular-nums">
            {formatAmount(total.value)} {total.currency_code}
          </td>
        </tr>
      </tfoot>
    </table>
  );
}
