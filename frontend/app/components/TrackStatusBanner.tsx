/**
 * Safety car, virtual safety car or red flag. Battles are greyed out under
 * all three: the field is neutralised, so a gap is not a fight.
 */
import type { TrackStatus } from '../types/api'

const BANNERS: Partial<Record<TrackStatus, { text: string; className: string }>> = {
  sc: { text: 'Safety Car', className: 'bg-sc text-ink-950' },
  vsc: { text: 'Virtual Safety Car', className: 'bg-sc text-ink-950' },
  red: { text: 'Red Flag', className: 'bg-red-700 text-white' },
}

export function isNeutralised(status: TrackStatus | null | undefined): boolean {
  return status === 'sc' || status === 'vsc' || status === 'red'
}

interface TrackStatusBannerProps {
  status: TrackStatus | null | undefined
  stoppage?: boolean | null
}

export function TrackStatusBanner({ status, stoppage }: TrackStatusBannerProps) {
  const banner = status ? BANNERS[status] : undefined
  if (!banner) return null

  return (
    <div role="status" className={`${banner.className} rounded-lg px-4 py-3 font-display text-lg font-extrabold uppercase tracking-[0.08em]`}>
      {banner.text}
      <span className="ml-3 font-sans text-sm font-medium normal-case tracking-normal">
        {stoppage ? 'Race suspended. Fast-forwarding to the restart.' : 'Battles paused until the restart.'}
      </span>
    </div>
  )
}
