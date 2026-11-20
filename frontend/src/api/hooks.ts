import { useCallback } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, download, query } from './client'
import type {
  AuthStatusOut, BrowseOut, CapacityHistoryOut, CronPreviewOut, DiffOut, DirectoryIn, DirectoryListOut,
  DirectorySummaryOut, EngineListOut, ProgressOut, ScanListOut, ScanOut, SettingsIn, SettingsOut, TreeOut,
  VersionOut, WhatsNewOut,
} from './schema.gen'

/** How often screens refresh: every 2 s while a scan is queued or running, otherwise every 30 s. */
export const ACTIVE_REFRESH = 2_000
export const IDLE_REFRESH = 30_000

export const useAuthStatus = () => useQuery({ queryKey: ['auth'], queryFn: () => api<AuthStatusOut>('/auth/status'), staleTime: Infinity })

export const useWhatsNew = () => useQuery({ queryKey: ['whats-new'], queryFn: () => api<WhatsNewOut>('/whats-new'), staleTime: Infinity })

export const useVersion = () => useQuery({ queryKey: ['version'], queryFn: () => api<VersionOut>('/version'), staleTime: Infinity })

export function useDirectories(enabled = true) {
  return useQuery({
    queryKey: ['directories'],
    queryFn: () => api<DirectoryListOut>('/directories'),
    enabled,
    refetchInterval: result => (result.state.data?.items.some(item => item.progress) ? ACTIVE_REFRESH : IDLE_REFRESH),
  })
}

export function useOverview(id: number) {
  return useQuery({
    queryKey: ['overview', id],
    queryFn: () => api<DirectorySummaryOut>(`/directories/${id}/overview`),
    refetchInterval: result => (result.state.data?.progress ? ACTIVE_REFRESH : IDLE_REFRESH),
  })
}

export function useTree(id: number, path: string, scan: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ['tree', id, scan, path],
    queryFn: () => api<TreeOut>(`/directories/${id}/tree${query({ path, scan })}`),
    enabled,
    placeholderData: keepPreviousData,
    retry: false,
  })
}

export function useDiff(id: number, path: string, from: number | null, to: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ['diff', id, from, to, path],
    queryFn: () => api<DiffOut>(`/directories/${id}/diff${query({ path, from, to })}`),
    enabled,
    placeholderData: keepPreviousData,
    retry: false,
  })
}

export const useScans = (id: number, days: number) => useQuery({
  queryKey: ['scans', id, days],
  queryFn: () => api<ScanListOut>(`/directories/${id}/scans${query({ days })}`),
})

export const useCapacityHistory = (id: number, days: number) => useQuery({
  queryKey: ['capacity', id, days],
  queryFn: () => api<CapacityHistoryOut>(`/directories/${id}/capacity${query({ days })}`),
})

export const useEngines = () => useQuery({ queryKey: ['engines'], queryFn: () => api<EngineListOut>('/engines'), staleTime: 60_000 })

export const useSettings = () => useQuery({ queryKey: ['settings'], queryFn: () => api<SettingsOut>('/settings') })

export const useBrowse = (path: string | null) => useQuery({
  queryKey: ['browse', path],
  queryFn: () => api<BrowseOut>(`/browse${query({ path })}`),
  placeholderData: keepPreviousData,
  retry: false,
})

export const useCronPreview = (expression: string | null) => useQuery({
  queryKey: ['cron', expression],
  queryFn: () => api<CronPreviewOut>(`/cron/preview${query({ expression })}`),
  enabled: Boolean(expression),
  retry: false,
  staleTime: 60_000,
})

/** Refreshes everything that shows a directory after it changed or a scan finished. */
export function useRefreshDirectory() {
  const client = useQueryClient()
  return useCallback(async (id?: number) => {
    await client.invalidateQueries({ queryKey: ['directories'] })
    if (id === undefined) return
    for (const key of ['overview', 'tree', 'diff', 'scans', 'capacity']) await client.invalidateQueries({ queryKey: [key, id] })
  }, [client])
}

export function useScanNow() {
  const refresh = useRefreshDirectory()
  return useMutation({
    mutationFn: (id: number) => api<ProgressOut>(`/directories/${id}/scan`, 'POST'),
    onSuccess: (_, id) => refresh(id),
  })
}

export function useCancelScan() {
  const refresh = useRefreshDirectory()
  return useMutation({
    mutationFn: (scan: { id: number; directory: number }) => api<ScanOut>(`/scans/${scan.id}/cancel`, 'POST'),
    onSuccess: (_, scan) => refresh(scan.directory),
  })
}

/** Downloads a scan's log file; a log that expired meanwhile refreshes the scans on screen. */
export function useDownloadLog() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (scan: number) => download(`/scans/${scan}/log`),
    onError: async () => {
      for (const key of ['scans', 'overview']) await client.invalidateQueries({ queryKey: [key] })
    },
  })
}

export function useSaveDirectory() {
  const refresh = useRefreshDirectory()
  return useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: DirectoryIn }) =>
      id === null ? api<DirectorySummaryOut>('/directories', 'POST', body) : api<DirectorySummaryOut>(`/directories/${id}`, 'PUT', body),
    onSuccess: result => refresh(result.directory.id),
  })
}

export function useDeleteDirectory() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api<void>(`/directories/${id}`, 'DELETE'),
    onSuccess: async (_, id) => {
      for (const key of ['overview', 'tree', 'diff', 'scans', 'capacity']) client.removeQueries({ queryKey: [key, id] })
      await client.invalidateQueries({ queryKey: ['directories'] })
    },
  })
}

export function useOrderDirectories() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (ids: number[]) => api<DirectoryListOut>('/directories/order', 'PUT', { ids }),
    onSuccess: result => client.setQueryData(['directories'], result),
  })
}

export function useSaveSettings() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: SettingsIn) => api<SettingsOut>('/settings', 'PUT', body),
    onSuccess: result => client.setQueryData(['settings'], result),
  })
}
