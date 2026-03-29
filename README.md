# GAAA - Google Ads AI Agents

**Your AI agent just learned Google Ads.**

```python
# What your AI agent sees:
gads.create_search_campaign('Summer Sale', budget_thb=500)
# {'ok': True, 'campaign_id': '123456789'}
```

```python
# What Google wants you to write:
# 50+ lines of protobuf, enums, field masks, resource names, micros conversion...
```

One file. Zero dependencies beyond `google-ads`. Plain dict in, plain dict out - exactly what LLMs and AI agents need.

GAAA (`gads.py`) turns the entire Google Ads API into simple function calls that any AI agent - Claude, GPT, or your custom automation - can use to create campaigns, manage bids, add keywords, and pull reports. Drop it in, `import gads`, done.

## Requirements

- Python 3.9+
- `pip install google-ads`
- A Google Ads API developer token ([apply here](https://developers.google.com/google-ads/api/docs/get-started/dev-token))
- OAuth2 credentials (client ID, client secret, refresh token)

## Setup

1. Clone this repo
2. Copy the example config:
   ```bash
   cp google-ads.yaml.example google-ads.yaml
   ```
3. Fill in your credentials in `google-ads.yaml`
4. Done.

Alternatively, set the `GOOGLE_ADS_YAML` environment variable to point to your config file anywhere on disk.

## Quick Start

```python
import gads

# List campaigns (last 30 days)
campaigns = gads.list_campaigns()

# Create a Search campaign (starts paused)
result = gads.create_search_campaign('Summer Sale', budget_thb=500)
# {'ok': True, 'campaign_id': '123456', 'budget_id': '789', ...}

# Create ad group
gads.create_ad_group(result['campaign_id'], 'Brand Keywords', cpc_bid_thb=3.0)

# Add keywords
gads.add_keywords('AD_GROUP_ID', ['running shoes', 'sport shoes'], match_type='PHRASE')

# Create a Responsive Search Ad
gads.create_rsa('AD_GROUP_ID',
    headlines=['Buy Running Shoes', 'Free Shipping', 'Shop Now'],
    descriptions=['Best prices on running shoes', 'Free delivery nationwide'],
    final_url='https://example.com/shoes')

# Performance report
report = gads.campaign_performance(days=7)
```

## Available Functions

### Campaigns
| Function | Description |
|----------|-------------|
| `create_search_campaign(name, budget_thb, ...)` | Create a Search campaign |
| `create_display_campaign(name, budget_thb, ...)` | Create a Display campaign |
| `create_shopping_campaign(name, budget_thb, merchant_id, ...)` | Create a Shopping campaign |
| `create_pmax_campaign(name, budget_thb, ...)` | Create a Performance Max campaign |
| `list_campaigns(customer_id, days, status_filter)` | List campaigns with metrics |
| `pause_campaign(campaign_id)` | Pause a campaign |
| `enable_campaign(campaign_id)` | Enable a campaign |
| `update_campaign_budget(campaign_id, new_budget_thb)` | Update daily budget |
| `delete_campaign(campaign_id)` | Delete a campaign |

### Ad Groups
| Function | Description |
|----------|-------------|
| `create_ad_group(campaign_id, name, cpc_bid_thb)` | Create an ad group |
| `list_ad_groups(campaign_id)` | List ad groups in a campaign |
| `pause_ad_group(ad_group_id)` | Pause an ad group |
| `enable_ad_group(ad_group_id)` | Enable an ad group |
| `set_ad_group_bid(ad_group_id, cpc_bid_thb)` | Update CPC bid |

### Ads
| Function | Description |
|----------|-------------|
| `create_rsa(ad_group_id, headlines, descriptions, final_url)` | Create Responsive Search Ad |
| `create_display_ad(ad_group_id, headlines, descriptions, business_name, final_url, image_url)` | Create Responsive Display Ad |
| `list_ads(ad_group_id)` | List ads in an ad group |
| `pause_ad(ad_id, ad_group_id)` | Pause an ad |

### Keywords
| Function | Description |
|----------|-------------|
| `add_keywords(ad_group_id, keywords, match_type)` | Add keywords (BROAD/PHRASE/EXACT) |
| `list_keywords(ad_group_id)` | List keywords with metrics |
| `remove_keyword(criterion_id, ad_group_id)` | Remove a keyword |

### Performance Max
| Function | Description |
|----------|-------------|
| `create_asset_group(campaign_id, name, final_url)` | Create asset group for PMax |
| `add_pmax_assets(asset_group_id, assets)` | Add headlines, descriptions, images |

### Reporting
| Function | Description |
|----------|-------------|
| `campaign_performance(customer_id, days)` | Campaign performance report |
| `ad_group_performance(campaign_id, customer_id, days)` | Ad group performance |
| `keyword_performance(campaign_id, customer_id, days)` | Keyword performance |

## Return Format

All mutation functions return:
```python
{'ok': True, 'campaign_id': '123', ...}   # success
{'ok': False, 'error': '...', 'error_type': 'BUDGET_ERROR'}  # failure
```

Listing/report functions return `list[dict]`.

## CLI

```bash
python3 gads.py list_campaigns '{"days": 7}'
python3 gads.py create_search_campaign '{"name": "Test", "budget_thb": 100}'
python3 gads.py campaign_performance
```

## Currency

All public-facing values use your local currency unit (e.g., THB). Micros conversion is handled internally. Customize `_thb_to_micros` / `_micros_to_thb` if you use a different currency.

## Geo & Language Targeting

Default targets are Thailand/Thai. Edit `GEO_TARGETS` and `LANGUAGE_TARGETS` dicts in the source for your market. Constants are public Google Ads API IDs - find yours at [Google Ads API Geo Target docs](https://developers.google.com/google-ads/api/reference/data/geotargets).

## License

MIT
