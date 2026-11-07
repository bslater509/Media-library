import { useState, type FormEvent, type ReactNode } from 'react'
import { KeyRound, Lock } from 'lucide-react'
import { ApiError } from '../../lib/api'
import { useAuthLogin, useAuthSession } from '../../lib/queries'
import { Button, Card, CardBody, CardHeader, CardTitle, Input, Spinner } from '../ui'

/**
 * Shared-password gate for the v2 client.
 *
 * While the current app hosts v2, its own middleware answers 401 before this
 * component can render, so the browser is redirected to that app's sign-in
 * page. Standalone (after cutover), the SPA loads and this screen asks for the
 * password in place.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const session = useAuthSession()
  const login = useAuthLogin()
  const [password, setPassword] = useState('')

  if (session.isPending) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-bg text-fg">
        <Spinner />
      </div>
    )
  }

  if (session.isError) {
    const error = session.error
    const unauthorized = error instanceof ApiError && error.status === 401
    if (unauthorized) {
      const next = `${window.location.pathname}${window.location.search}`
      window.location.assign(`/login?next=${encodeURIComponent(next)}`)
      return (
        <div className="flex min-h-dvh items-center justify-center bg-bg text-fg">
          <Spinner />
        </div>
      )
    }
    return (
      <div className="flex min-h-dvh items-center justify-center bg-bg px-4 text-fg">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle>Cannot reach the library</CardTitle>
          </CardHeader>
          <CardBody className="flex flex-col gap-3 text-sm text-fg-muted">
            <p>{error instanceof Error ? error.message : 'The server did not answer.'}</p>
            <Button variant="secondary" onClick={() => void session.refetch()}>
              Try again
            </Button>
          </CardBody>
        </Card>
      </div>
    )
  }

  const data = session.data
  if (!data || !data.enabled || data.authenticated) {
    return <>{children}</>
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!password) return
    login.mutate(password, { onSuccess: () => setPassword('') })
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg px-4 py-10 text-fg">
      <main className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-1 text-center">
          <span className="mb-2 flex size-11 items-center justify-center rounded-xl bg-accent-soft text-accent">
            <Lock aria-hidden="true" className="size-5" />
          </span>
          <h1 className="text-lg font-semibold tracking-tight">Media Library</h1>
          <p className="text-sm text-fg-muted">
            Enter the shared password to open the library.
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <KeyRound aria-hidden="true" className="size-4 text-fg-subtle" />
              Sign in
            </CardTitle>
          </CardHeader>
          <CardBody>
            <form className="flex flex-col gap-3" onSubmit={onSubmit}>
              <label className="flex flex-col gap-1.5 text-xs font-medium text-fg-muted">
                Password
                <Input
                  type="password"
                  autoFocus
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  aria-invalid={login.isError || undefined}
                />
              </label>
              {login.isError ? (
                <p className="text-xs text-danger" role="alert">
                  {login.error instanceof Error
                    ? login.error.message
                    : 'Incorrect password'}
                </p>
              ) : null}
              <Button type="submit" disabled={!password || login.isPending}>
                {login.isPending ? 'Signing in…' : 'Sign in'}
              </Button>
            </form>
          </CardBody>
        </Card>

        <p className="mt-4 text-center text-[11px] text-fg-subtle">
          This browser stays signed in until the shared password changes.
        </p>
      </main>
    </div>
  )
}
