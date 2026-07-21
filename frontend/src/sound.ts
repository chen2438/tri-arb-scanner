export const SOUND_STORAGE_KEY = "tri-arb-sound-enabled";
export const ROUTE_SOUND_DEDUPE_MS = 30_000;

export class RouteSoundGate {
  private readonly lastPlayed = new Map<string, number>();

  shouldPlay(routeId: string, now: number): boolean {
    const previous = this.lastPlayed.get(routeId);
    if (previous !== undefined && now - previous < ROUTE_SOUND_DEDUPE_MS) return false;
    this.lastPlayed.set(routeId, now);
    return true;
  }
}

export function playOpportunityTone(): void {
  try {
    const AudioContextClass = window.AudioContext;
    if (!AudioContextClass) return;
    const context = new AudioContextClass();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(620, context.currentTime);
    oscillator.frequency.exponentialRampToValueAtTime(880, context.currentTime + 0.16);
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.12, context.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.28);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.3);
    oscillator.addEventListener("ended", () => void context.close(), { once: true });
  } catch {
    // Browsers may reject audio before a user gesture; alerts must never break the scanner UI.
  }
}
