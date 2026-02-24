/**
 * Simple sparkline chart for visualizing gap trends.
 */
interface SparklineProps {
  data: number[]
}

export function Sparkline({ data }: SparklineProps) {
  if (data.length === 0) return null

  const width = 200
  const height = 40
  const padding = 4

  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1

  const points = data.map((value, index) => {
    const x = (index / (data.length - 1)) * (width - 2 * padding) + padding
    const y = height - padding - ((value - min) / range) * (height - 2 * padding)
    return `${x},${y}`
  }).join(' ')

  // Determine color based on trend (gap closing = green, gap opening = red)
  const trend = data[data.length - 1] - data[0]
  const color = trend < 0 ? '#10b981' : '#ef4444'

  return (
    <svg width={width} height={height} className="w-full">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
