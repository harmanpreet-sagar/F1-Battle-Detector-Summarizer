/**
 * Safety car, virtual safety car or red flag. Battles are greyed out under
 * all three: the field is neutralised, so a gap is not a fight.
 */
import type { TrackStatus } from '../types/api'

const BANNERS: Partial<Record<TrackStatus, { text: string; className: string }>> = {
  sc: { text: 'Safety Car', className: 'bg-yellow-500 text-black' },
  vsc: { text: 'Virtual Safety Car', className: 'bg-yellow-500 text-black' },
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
    <div role="status" className={`${banner.className} mb-6 rounded-lg px-4 py-3 font-bold uppercase tracking-wide`}>
      {banner.text}
      <span className="ml-3 text-sm font-normal normal-case">
        {stoppage ? 'Race suspended. Fast-forwarding to the restart.' : 'Battles paused until the restart.'}
      </span>
    </div>
  )
}
