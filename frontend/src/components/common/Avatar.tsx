'use client';

import { useState } from 'react';

export function Avatar({ url, name }: { url?: string | null; name: string }) {
  const [failed, setFailed] = useState<string | null>(null);
  return url && failed !== url ? (
    // The address is user supplied; do not proxy it through the image optimizer.
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt={`${name}的头像`} referrerPolicy="no-referrer" onError={() => setFailed(url)} className="h-10 w-10 shrink-0 object-cover" />
  ) : <span className="grid h-10 w-10 shrink-0 place-items-center bg-ink text-on-ink" aria-label={`${name}的默认头像`}>{name.charAt(0) || 'U'}</span>;
}
