/**
 * Connection status banner showing data freshness.
 */
import type { ConnectionStatus as Status } from '../types/api'

interface ConnectionStatusProps {
  status: Status
}

export function ConnectionStatus({ status }: ConnectionStatusProps) {
  const statusConfig = {
    live: {
      color: 'bg-green-600',
      text: 'LIVE',
      icon: '🟢',
    },
    delayed: {
      color: 'bg-yellow-600',
      text: 'DELAYED',
      icon: '🟡',
    },
    reconnecting: {
      color: 'bg-red-600',
      text: 'RECONNECTING',
      icon: '🔴',
    },
  }

  const config = statusConfig[status]

  return (
    <div className={`${config.color} text-white px-4 py-2 text-center font-semibold`}>
      {config.icon} {config.text}
    </div>
  )
}
