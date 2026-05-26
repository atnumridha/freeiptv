# Free Channel Source Review

Generated: 2026-05-26

Reviewed the direct free IPTV playlist URLs from the pasted article against
`south_asia_cricket.m3u`. The review used the existing South Asia language,
category, Arabic-channel, Free-TV India skip-list, HLS probe, duplicate, and
screenshot-validation rules.

| Source | URL | Result |
| --- | --- | --- |
| IPTV-Org | `https://iptv-org.github.io/iptv/index.m3u` | Already covered through the IPTV-Org country, language, and sports playlist inputs. |
| Free-TV | `https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8` | Already included in the South Asia scraper. |
| Samsung TV Plus | `https://www.apsattv.com/ssungusa.m3u` | Loaded, but produced no in-scope South Asia or cricket channels after filters. |
| Pluto TV | `https://i.mjh.nz/PlutoTV/all.m3u8` | Unavailable during review: HTTP 404. |
| Roku Channel | `https://www.apsattv.com/rok.m3u` | Loaded, but produced no in-scope South Asia or cricket channels after filters. |
| XUMO Play | `https://www.apsattv.com/xumo.m3u` | Loaded, but produced no in-scope South Asia or cricket channels after filters. |
| LG Channels | `https://www.apsattv.com/lg.m3u` | Loaded two in-scope channels, but both were duplicates of channels already in the playlist. Not added. |
| DistroTV | `https://www.apsattv.com/distro.m3u` | Added as a reviewed source. It contributed `News Marathi 24X7` and `Top News Marathi` after probe and screenshot validation. Duplicate `KTV Bangla` was skipped because the existing `KTV Bangla (720p)` stream already passed validation. |
| LocalNow | `https://www.apsattv.com/localnow.m3u` | Loaded, but produced no in-scope South Asia or cricket channels after filters. |
| EPGHub | `https://epghub.xyz/` | Not added because it is a generator site, not a static playlist URL. |

Final result after this review:

- Working screenshot-verified channels: 208.
- DistroTV additions retained: `News Marathi 24X7`, `Top News Marathi`.
- Duplicate stream URLs in final playlist: 0.
- Distro duplicate replacements: 0, because existing same-name channels still
  passed validation.
