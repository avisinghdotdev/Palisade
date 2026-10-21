import type { Product } from "@/api/client";

import { ProductLightbox } from "@/components/product-lightbox";

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

/** "$120" for whole dollars, "$89.50" otherwise. */
function formatPrice(value: string): string {
  const amount = Number(value);
  return (Number.isInteger(amount) ? dollars : dollarsAndCents).format(amount);
}

export function ProductTile({ product }: { product: Product }) {
  return (
    <article>
      <ProductLightbox product={product} />
      <div className="mt-3">
        <p className="text-xs font-semibold tracking-wide text-primary uppercase">
          {product.brand}
        </p>
        <h2 className="mt-0.5 font-medium">{product.name}</h2>
        <p className="text-sm text-muted-foreground">{product.category}</p>
        <p className="mt-1.5 font-semibold">
          {formatPrice(product.price.value)}{" "}
          <span className="text-xs font-normal text-muted-foreground">
            {product.price.currency_code}
          </span>
        </p>
      </div>
    </article>
  );
}
