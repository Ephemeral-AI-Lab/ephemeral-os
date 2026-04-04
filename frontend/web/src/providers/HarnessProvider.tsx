import { createContext, useContext, type ReactNode } from 'react'
import { useConnected } from '../lib/hooks'

const HarnessContext = createContext<{ connected: boolean }>({ connected: false })

export function useHarness() {
  return useContext(HarnessContext)
}

export function HarnessProvider({ children }: { children: ReactNode }) {
  const connected = useConnected()

  return (
    <HarnessContext.Provider value={{ connected }}>
      {children}
    </HarnessContext.Provider>
  )
}
