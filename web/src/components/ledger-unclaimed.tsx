import type { UnclaimedPayment } from "@/api/client";

import { ledgerAmount } from "@/ledger-labels";
import { formatUtc } from "@/money";

const HEADINGS = [
  "PayPal Payment",
  "Taken",
  "Amount",
  "Order Number PayPal Holds",
  "Custom ID",
];

/** The payments PayPal took for the merchant that the store holds no paid order for. */
export function UnclaimedPayments({
  payments,
}: {
  payments: UnclaimedPayment[];
}) {
  return (
    <section aria-labelledby="unclaimed" className="mt-10">
      <h2 className="text-base font-semibold" id="unclaimed">
        Payments No Paid Order Names
      </h2>
      <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
        PayPal took these payments for the merchant since the first paid order,
        and the store holds no paid order for them. A payment made outside this
        store&apos;s database, such as a test run against another database,
        shows up here.
      </p>
      <div className="mt-3 overflow-x-auto rounded-xl border bg-card">
        <table className="w-full text-left text-sm">
          <thead className="bg-secondary text-[13px] text-muted-foreground">
            <tr>
              {HEADINGS.map((heading) => (
                <th
                  className="px-4 py-2.5 font-medium whitespace-nowrap"
                  key={heading}
                  scope="col"
                >
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {payments.map((payment) => (
              <tr key={payment.id}>
                <td className="px-4 py-3 font-mono text-xs">{payment.id}</td>
                <td className="px-4 py-3 whitespace-nowrap">
                  {formatUtc(payment.at)}
                </td>
                <td className="px-4 py-3 tabular-nums">
                  {ledgerAmount(payment.amount)}
                </td>
                <td className="px-4 py-3">
                  {payment.invoice_id ?? (
                    <span className="text-muted-foreground">None</span>
                  )}
                </td>
                <td className="px-4 py-3 font-mono text-xs break-all">
                  {payment.custom_id ?? (
                    <span className="font-sans text-sm text-muted-foreground">
                      None
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
