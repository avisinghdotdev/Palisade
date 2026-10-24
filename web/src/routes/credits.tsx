import { useQuery } from "@tanstack/react-query";

import { productsOptions } from "@/api/client/@tanstack/react-query.gen";
import { ExternalLink } from "@/components/external-link";
import { HackathonLink } from "@/components/hackathon-link";
import { ShopHeader } from "@/components/shop-header";
import { Button } from "@/components/ui/button";
import { pageTitle } from "@/page-title";

import type { Route } from "./+types/credits";

export const meta: Route.MetaFunction = () => [
  { title: pageTitle("Photo Credits", "Shop") },
];

export default function Credits() {
  const products = useQuery(productsOptions());

  return (
    <div className="theme-shop flex min-h-dvh flex-col bg-background text-foreground">
      <ShopHeader to="/shop" />
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8 sm:px-6">
        <h1 className="text-2xl font-semibold tracking-tight">
          About the Shoes and Photos
        </h1>
        <div className="mt-4 space-y-3 text-sm leading-relaxed">
          <p>
            Ridgeline Running is a demo store built for the <HackathonLink /> to
            show Palisade, a trust and dispute layer for purchases that AI
            agents make with PayPal. Not for sale. Every payment runs in the
            PayPal sandbox with test money.
          </p>
          <p>
            The shoes are real models from Nike, Adidas, Hoka, Brooks, New
            Balance, and Asics, named so the catalog is recognizable. Those
            names and the logos in the photos belong to their makers, who have
            no part in this project and do not endorse it.
          </p>
          <p>
            Each photo is an Unsplash picture of the shoe, used under the{" "}
            <ExternalLink href="https://unsplash.com/license">
              Unsplash License
            </ExternalLink>
            , which allows free use without permission. The photographers are
            credited below.
          </p>
        </div>
        <h2 className="mt-8 text-lg font-semibold">Photo Credits</h2>
        {products.isPending && (
          <p className="mt-2 text-sm text-muted-foreground">Loading…</p>
        )}
        {products.isError && (
          <p className="mt-2 flex items-center gap-3 text-sm">
            The credits did not load.
            <Button
              onClick={() => {
                void products.refetch();
              }}
              size="sm"
              variant="outline"
            >
              Try Again
            </Button>
          </p>
        )}
        {products.data && (
          <table className="mt-3 w-full text-sm">
            <thead className="text-left text-muted-foreground">
              <tr className="border-b">
                <th className="py-2 pr-4 font-medium" scope="col">
                  Shoe
                </th>
                <th className="py-2 pr-4 font-medium" scope="col">
                  Photo
                </th>
                <th className="py-2 font-medium" scope="col">
                  Photographer
                </th>
              </tr>
            </thead>
            <tbody>
              {products.data.map((product) => (
                <tr className="border-b" key={product.id}>
                  <th className="py-2 pr-4 text-left font-normal" scope="row">
                    {product.brand} {product.name}
                  </th>
                  <td className="py-2 pr-4">
                    <ExternalLink href={product.photo.url}>
                      On Unsplash
                      <span className="sr-only">
                        : {product.brand} {product.name}
                      </span>
                    </ExternalLink>
                  </td>
                  <td className="py-2">
                    <ExternalLink href={product.photo.by_url}>
                      {product.photo.by}
                    </ExternalLink>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </div>
  );
}
