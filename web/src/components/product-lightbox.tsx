import { useState } from "react";

import type { Product } from "@/api/client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { photoSrcSet } from "@/product-photos";

/**
 * The product photo as a tile that opens large over the blurred page. The tile pulses until the
 * photo has loaded, then the photo fades in. The tile picks a WebP copy by `sizes`, the tile's
 * width in the grid; the large view shows the full JPEG. `eager` is for tiles in view on arrival,
 * which a lazy load would hold back until layout.
 */
export function ProductLightbox({
  product,
  sizes,
  eager,
}: {
  product: Product;
  sizes: string;
  eager: boolean;
}) {
  const alt = `${product.brand} ${product.name}`;
  // A photo that fails to load also settles, so its alt text shows instead of a pulse forever.
  const [settled, setSettled] = useState(false);
  const settle = () => {
    setSettled(true);
  };

  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          aria-label={`View ${alt} larger`}
          className={`group block aspect-square w-full cursor-pointer overflow-hidden rounded-md bg-muted outline-none focus-visible:ring-3 focus-visible:ring-ring/50 ${settled ? "" : "animate-pulse"}`}
          type="button"
        >
          <img
            alt={alt}
            className={`size-full object-cover transition duration-300 group-hover:scale-[1.04] ${settled ? "opacity-100" : "opacity-0"}`}
            decoding="async"
            loading={eager ? "eager" : "lazy"}
            onError={settle}
            onLoad={settle}
            sizes={sizes}
            src={product.image_path}
            srcSet={photoSrcSet(product.image_path)}
          />
        </button>
      </DialogTrigger>
      <DialogContent className="w-[min(96vw,64rem)] p-3 sm:max-w-5xl">
        <DialogHeader className="pr-10">
          <DialogTitle>{alt}</DialogTitle>
          <DialogDescription className="sr-only">
            Product photo
          </DialogDescription>
        </DialogHeader>
        <img
          alt={alt}
          className="mx-auto max-h-[78dvh] w-auto rounded-md object-contain"
          src={product.image_path}
        />
      </DialogContent>
    </Dialog>
  );
}
