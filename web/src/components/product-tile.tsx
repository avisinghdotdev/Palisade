import { Check } from "lucide-react";
import { useEffect, useState } from "react";

import type { Product } from "@/api/client";

import { ProductLightbox } from "@/components/product-lightbox";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCart } from "@/hooks/use-cart";
import { formatMoney } from "@/money";

export function ProductTile({
  product,
  walletId,
}: {
  product: Product;
  walletId: string | undefined;
}) {
  const { cart, setLine } = useCart(walletId);
  const [size, setSize] = useState<null | string>(null);
  const [sizesOpen, setSizesOpen] = useState(false);
  const [added, setAdded] = useState(false);

  useEffect(() => {
    if (!added) return;
    const timer = setTimeout(() => {
      setAdded(false);
    }, 1500);
    return () => {
      clearTimeout(timer);
    };
  }, [added]);

  const inCart =
    cart?.lines.find(
      (line) => line.product_id === product.id && line.size === size,
    )?.quantity ?? 0;

  const add = () => {
    // Without a size there is nothing to add yet, so the sizes open for a choice.
    if (size === null) {
      setSizesOpen(true);
      return;
    }
    setLine.mutate(
      { product_id: product.id, size, quantity: inCart + 1 },
      {
        onSuccess: () => {
          setAdded(true);
        },
      },
    );
  };

  return (
    <article className="flex h-full flex-col @container">
      <ProductLightbox product={product} />
      <div className="mt-3 flex flex-1 flex-col">
        <p className="text-xs font-semibold tracking-wide text-primary uppercase">
          {product.brand}
        </p>
        <h2 className="mt-0.5 font-medium">{product.name}</h2>
        <p className="text-sm text-muted-foreground">{product.category}</p>
        <p className="mt-1.5 font-semibold">
          {formatMoney(product.price.value)}{" "}
          <span className="text-xs font-normal text-muted-foreground">
            {product.price.currency_code}
          </span>
        </p>
        {/* Pinned to the tile's foot so a row lines up when a name wraps; stacked on a narrow phone tile. */}
        <div className="mt-auto flex flex-col gap-2 pt-2 @[13.5rem]:flex-row">
          <Select
            onOpenChange={setSizesOpen}
            onValueChange={setSize}
            open={sizesOpen}
            value={size ?? undefined}
          >
            <SelectTrigger
              aria-label={`US size for ${product.brand} ${product.name}`}
              className="h-7 w-full text-xs @[13.5rem]:w-auto @[13.5rem]:flex-1"
              size="sm"
            >
              <SelectValue placeholder="US Size" />
            </SelectTrigger>
            <SelectContent>
              {product.sizes.map((option) => (
                <SelectItem key={option} value={option}>
                  US {option}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            disabled={walletId === undefined || setLine.isPending}
            onClick={add}
            size="sm"
          >
            {added ? (
              <>
                <Check aria-hidden /> Added
              </>
            ) : (
              "Add to Cart"
            )}
          </Button>
        </div>
      </div>
    </article>
  );
}
