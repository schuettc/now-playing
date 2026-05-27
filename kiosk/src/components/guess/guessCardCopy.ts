export function primaryButtonLabel(guess: { title: string }): string {
  const title = (guess.title || '').trim();
  // Why: defensive fallback for if the backend ever publishes a guess
  // with an empty title mid-deploy — the kiosk shouldn't render a CTA
  // ending in a trailing space.
  return title === '' ? "Yes, that's it" : `Yes, that's ${title}`;
}

export function pickManuallyLabel(): string {
  return 'Pick a track manually →';
}
