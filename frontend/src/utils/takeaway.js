// Plain-language "so what?" helpers — turn a raw number into what it MEANS, so a
// newcomer learns from every metric instead of decoding jargon. Product language
// is English (obsyd.dev). Descriptive: deviation vs history, never a forecast.

// z-score → plain phrase (mirrors the CALM/ELEVATED/STRESSED σ bands).
export function zPhrase(z) {
  if (z == null) return 'no baseline yet'
  const az = Math.abs(z)
  if (az >= 3) return z > 0 ? 'unusually high vs its own history' : 'unusually low vs its own history'
  if (az >= 2) return z > 0 ? 'well above its normal range' : 'well below its normal range'
  if (az >= 1) return z > 0 ? 'a bit above normal' : 'a bit below normal'
  return 'within its normal range'
}

// One plain sentence for the whole desk state — the anchor's "answer".
export function stateSentence(state, drivers = []) {
  const lead = {
    STRESSED: 'Under stress',
    ELEVATED: 'Somewhat elevated',
    CALM: 'Calm',
  }[state] || 'Calm'
  const why = drivers.filter(Boolean).slice(0, 2).join(' · ')
  return why ? `${lead} — ${why}.` : `${lead} — nothing unusual right now.`
}

// Spark spread → what it means for gas generation economics.
export function sparkPhrase(v) {
  if (v == null) return null
  return v >= 0
    ? 'gas-fired power is profitable to run'
    : 'gas-fired power is uneconomic (running at a loss)'
}

// Residual-load level phrase (GW) — the demand dispatchable plants must cover.
export function residualPhrase(z) {
  if (z == null) return 'the demand thermal plants must cover'
  const az = Math.abs(z)
  if (az >= 2 && z < 0) return 'very low — wind & solar are covering most of demand (prices tend soft)'
  if (az >= 2 && z > 0) return 'high — thermal plants must cover a lot (prices tend firm)'
  return 'the demand wind & solar do not cover'
}
