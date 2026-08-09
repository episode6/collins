// Modified from the original agent-session-manager
// (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
// fork. Last modified: 2026-08-09. Full change history: git log for this file.
import { defineConfig } from 'vitepress'

// https://vitepress.dev/reference/site-config
export default defineConfig({
  title: 'Collins',
  description: 'An opinionated AI-native workspace for Claude — an agent-first IDE and agent orchestrator that browses, names, and resumes your Claude Code sessions in embedded terminals.',
  lang: 'en-US',
  // Must match the GitHub repo name (GitHub Pages project path).
  base: '/collins/',
  // Terminal look: default to dark (readers can still toggle light).
  appearance: 'dark',
  lastUpdated: true,
  cleanUrls: true,

  head: [
    ['meta', { name: 'theme-color', content: '#D97757' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:title', content: 'Collins' }],
    ['meta', {
      property: 'og:image',
      content: 'https://raw.githubusercontent.com/episode6/collins/main/data/banner.png',
    }],
  ],

  themeConfig: {
    // https://vitepress.dev/reference/default-theme-config
    nav: [
      { text: 'Guide', link: '/guide/introduction' },
      { text: 'Releases & Roadmap', link: '/releases' },
    ],

    sidebar: {
      '/guide/': [
        {
          text: 'Introduction',
          items: [
            { text: 'What is Collins?', link: '/guide/introduction' },
            { text: 'Getting Started', link: '/guide/getting-started' },
          ],
        },
        {
          text: 'Usage',
          items: [
            { text: 'Features', link: '/guide/features' },
            { text: 'Keyboard Shortcuts', link: '/guide/keyboard-shortcuts' },
            { text: 'How It Works', link: '/guide/how-it-works' },
            { text: 'FAQ', link: '/guide/faq' },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/episode6/collins' },
    ],

    editLink: {
      pattern: 'https://github.com/episode6/collins/edit/main/docs/:path',
      text: 'Edit this page on GitHub',
    },

    footer: {
      message: 'Unofficial community tool — not affiliated with or endorsed by Anthropic. Released under GPL-3.0. Forked from <a href="https://r4nd3l.github.io/agent-session-manager/">agent-session-manager</a> by Máté Molnár.',
      copyright: 'Copyright © 2026 Máté Molnár (original), episode6, Inc. (fork)',
    },

    search: { provider: 'local' },
  },
})
