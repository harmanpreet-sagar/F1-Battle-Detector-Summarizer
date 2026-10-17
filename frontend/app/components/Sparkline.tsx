/**
 * Gap trend line. Green when the gap is closing, red when it is opening; the
 * delta beside it carries the same meaning in text for anyone who cannot tell
 * the two colours apart.
 */
interface SparklineProps {
  data: number[]
  /** Drawing height in px; the line always fills its container's width. */
  height?: number
  /** Dashed line marking 1.0s (DRS / Overtake Mode range). */
  showOneSecond?: boolean
  label?: string
}

export function Sparkline({ data, height = 40, showOneSecond = false, label }: SparklineProps) {
  if (data.length < 2) return null

  const width = 800
  const pad = 6
  const values = showOneSecond ? [...data, 1.0] : data
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const y = (v: number) => height - pad - ((v - min) / range) * (height - 2 * pad)

  const points = data
    .map((value, index) => `${(index / (data.length - 1)) * (width - 2 * pad) + pad},${y(value)}`)
    .join(' ')

  const closing = data[data.length - 1] - data[0] < 0
  const colour = closing ? '#4ADE80' : '#F87171'
  const description =
    label ?? `Gap ${closing ? 'closing' : 'opening'} from ${data[0].toFixed(2)}s to ${data[data.length - 1].toFixed(2)}s`

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="block w-full"
      style={{ height }}
      role="img"
      aria-label={description}
    >
      {showOneSecond && (
        <line x1={0} x2={width} y1={y(1.0)} y2={y(1.0)} stroke="#2E2E3A" strokeDasharray="4 6" vectorEffect="non-scaling-stroke" />
      )}
      <polyline
        points={points}
        fill="none"
        stroke={colour}
        strokeWidth={height > 60 ? 3 : 2}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}
