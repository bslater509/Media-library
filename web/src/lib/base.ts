/**
 * Where the SPA is mounted.
 *
 * The same client bundle serves two deployments: beside the current app at
 * `/v2` and, after cutover, standalone at `/`. `main.tsx` boots from whichever
 * URL it was loaded on, and the absolute asset/API bases stay valid for both.
 */
const mountedAtV2 = /^\/v2(\/|$)/.test(window.location.pathname)

export const APP_BASENAME = mountedAtV2 ? '/v2' : '/'
export const IS_MOUNTED_PREVIEW = mountedAtV2
