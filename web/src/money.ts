const dollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});
const dollarsAndCents = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

/** "$120" for whole dollars, "$89.50" otherwise: the shop's price tags. */
export function formatMoney(value: string): string {
  const amount = Number(value);
  return (Number.isInteger(amount) ? dollars : dollarsAndCents).format(amount);
}

/** Always dollars and cents, "$120.00": receipts and ledgers. */
export function formatAmount(value: string): string {
  return dollarsAndCents.format(Number(value));
}

const utcTime = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "medium",
  timeZone: "UTC",
});

/** The merchant's view: one clock for every order, whoever placed it. */
export function formatUtc(value: string): string {
  return `${utcTime.format(new Date(value))} UTC`;
}

const localTime = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

/** The shopper's view: their own clock and time zone, as a till receipt prints it. */
export function formatLocal(value: string): string {
  return localTime.format(new Date(value));
}
