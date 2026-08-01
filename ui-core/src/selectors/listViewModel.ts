export type ListSurfaceStatus = 'ready' | 'loading' | 'error';

export function toListSurfaceStatus(
  status: 'idle' | 'loading' | 'ready' | 'error',
): ListSurfaceStatus {
  return status === 'loading' || status === 'error' ? status : 'ready';
}
