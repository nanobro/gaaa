"""
gads.py - Google Ads Management Module
Single-file Python wrapper for the Google Ads API.

Usage:
    import gads
    gads.create_search_campaign('My Campaign', budget_thb=500)

Setup:
    1. pip install google-ads
    2. Copy google-ads.yaml.example to google-ads.yaml and fill in your credentials
    3. Set GOOGLE_ADS_YAML env var if the file is not in the same directory as this script
"""

import datetime
import json
import os
import sys
import uuid
import warnings

# Suppress Python 3.9 FutureWarning from protobuf
warnings.filterwarnings('ignore', category=FutureWarning)

import logging
logging.getLogger('google.ads.googleads.client').setLevel(logging.ERROR)

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

# ============================================================
# Constants
# ============================================================

YAML_PATH = os.environ.get('GOOGLE_ADS_YAML',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'google-ads.yaml'))
API_VERSION = 'v20'

# Common geo target constants (Google Ads API public IDs)
GEO_TARGETS = {
    'Thailand': 'geoTargetConstants/2764',
    'Bangkok': 'geoTargetConstants/1012728',
}

# Common language constants (Google Ads API public IDs)
LANGUAGE_TARGETS = {
    'th': 'languageConstants/1044',
    'en': 'languageConstants/1000',
}

# ============================================================
# Internal Helpers
# ============================================================

_client = None
_customer_id = None


def _get_client():
    global _client
    if _client is None:
        _client = GoogleAdsClient.load_from_storage(YAML_PATH, version=API_VERSION)
    return _client


def _get_customer_id():
    """Get default customer ID from the YAML config (login_customer_id)."""
    global _customer_id
    if _customer_id is None:
        client = _get_client()
        _customer_id = client.login_customer_id
    return _customer_id


def _thb_to_micros(thb):
    return int(float(thb) * 1_000_000)


def _micros_to_thb(micros):
    return round(int(micros) / 1_000_000, 2)


def _resource_id(resource_name):
    """Extract numeric ID from resource_name like 'customers/123/campaigns/456' -> '456'"""
    return resource_name.split('/')[-1]


def _ok(data=None, **kwargs):
    result = {'ok': True}
    if data:
        result.update(data)
    result.update(kwargs)
    return result


def _fail(msg, error_type='UNKNOWN'):
    return {'ok': False, 'error': msg, 'error_type': error_type}


class AdsError(Exception):
    def __init__(self, message, error_type='UNKNOWN', request_id=''):
        self.error_type = error_type
        self.request_id = request_id
        super().__init__(message)


def _handle_ads_error(e):
    """Translate GoogleAdsException to a clean error dict."""
    error_map = {
        'authentication_error': ('AUTH_ERROR', 'Auth failed'),
        'authorization_error': ('AUTH_ERROR', 'Not authorized'),
        'quota_error': ('QUOTA_ERROR', 'Rate limit hit'),
        'campaign_budget_error': ('BUDGET_ERROR', 'Budget error'),
        'campaign_error': ('CAMPAIGN_ERROR', 'Campaign error'),
        'ad_error': ('AD_ERROR', 'Ad error'),
        'policy_finding_error': ('POLICY_ERROR', 'Policy violation'),
        'ad_group_criterion_error': ('KEYWORD_ERROR', 'Keyword error'),
    }
    for err in e.failure.errors:
        code = err.error_code
        msg = err.message
        for field_name, (error_type, prefix) in error_map.items():
            val = getattr(code, field_name, 0)
            if val and val != 0:
                return _fail(f'{prefix}: {msg}', error_type)
    first_msg = e.failure.errors[0].message if e.failure.errors else str(e)
    return _fail(f'API error (request {e.request_id}): {first_msg}')


def _set_field_mask(client, operation, paths):
    field_mask = client.get_type('FieldMask')
    for p in paths:
        field_mask.paths.append(p)
    operation.update_mask.CopyFrom(field_mask)


# ============================================================
# Campaign Operations
# ============================================================

def create_search_campaign(name, budget_thb, customer_id=None,
                           bidding='MANUAL_CPC', geo='Thailand', language='th',
                           start_date=None):
    """Create a Search campaign with budget, geo and language targeting."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        # 1. Create budget
        budget_service = client.get_service('CampaignBudgetService')
        budget_op = client.get_type('CampaignBudgetOperation')
        budget = budget_op.create
        budget.name = f'{name} Budget {uuid.uuid4().hex[:8]}'
        budget.amount_micros = _thb_to_micros(budget_thb)
        budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget_resp = budget_service.mutate_campaign_budgets(
            customer_id=customer_id, operations=[budget_op])
        budget_rn = budget_resp.results[0].resource_name

        # 2. Create campaign
        campaign_service = client.get_service('CampaignService')
        campaign_op = client.get_type('CampaignOperation')
        campaign = campaign_op.create
        campaign.name = name
        campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
        campaign.status = client.enums.CampaignStatusEnum.PAUSED
        campaign.campaign_budget = budget_rn

        if bidding == 'MANUAL_CPC':
            campaign.manual_cpc.enhanced_cpc_enabled = False
        elif bidding == 'MAXIMIZE_CLICKS':
            campaign.target_spend.target_spend_micros = 0
        elif bidding == 'MAXIMIZE_CONVERSIONS':
            campaign.maximize_conversions.target_cpa_micros = 0

        campaign.network_settings.target_google_search = True
        campaign.network_settings.target_search_network = True
        campaign.network_settings.target_partner_search_network = False
        campaign.network_settings.target_content_network = False

        sd = start_date or datetime.date.today().strftime('%Y-%m-%d')
        campaign.start_date = sd.replace('-', '')

        campaign_resp = campaign_service.mutate_campaigns(
            customer_id=customer_id, operations=[campaign_op])
        campaign_rn = campaign_resp.results[0].resource_name
        campaign_id = _resource_id(campaign_rn)

        # 3. Geo targeting
        if geo and geo in GEO_TARGETS:
            criterion_service = client.get_service('CampaignCriterionService')
            geo_op = client.get_type('CampaignCriterionOperation')
            geo_criterion = geo_op.create
            geo_criterion.campaign = campaign_rn
            geo_criterion.location.geo_target_constant = GEO_TARGETS[geo]
            criterion_service.mutate_campaign_criteria(
                customer_id=customer_id, operations=[geo_op])

        # 4. Language targeting
        if language and language in LANGUAGE_TARGETS:
            criterion_service = client.get_service('CampaignCriterionService')
            lang_op = client.get_type('CampaignCriterionOperation')
            lang_criterion = lang_op.create
            lang_criterion.campaign = campaign_rn
            lang_criterion.language.language_constant = LANGUAGE_TARGETS[language]
            criterion_service.mutate_campaign_criteria(
                customer_id=customer_id, operations=[lang_op])

        return _ok(campaign_id=campaign_id, budget_id=_resource_id(budget_rn),
                    resource_name=campaign_rn)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def create_display_campaign(name, budget_thb, customer_id=None,
                            bidding='MANUAL_CPC', geo='Thailand', language='th'):
    """Create a Display campaign."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        budget_service = client.get_service('CampaignBudgetService')
        budget_op = client.get_type('CampaignBudgetOperation')
        budget = budget_op.create
        budget.name = f'{name} Budget {uuid.uuid4().hex[:8]}'
        budget.amount_micros = _thb_to_micros(budget_thb)
        budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget_resp = budget_service.mutate_campaign_budgets(
            customer_id=customer_id, operations=[budget_op])
        budget_rn = budget_resp.results[0].resource_name

        campaign_service = client.get_service('CampaignService')
        campaign_op = client.get_type('CampaignOperation')
        campaign = campaign_op.create
        campaign.name = name
        campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.DISPLAY
        campaign.status = client.enums.CampaignStatusEnum.PAUSED
        campaign.campaign_budget = budget_rn

        if bidding == 'MANUAL_CPC':
            campaign.manual_cpc.enhanced_cpc_enabled = False
        elif bidding == 'MAXIMIZE_CLICKS':
            campaign.target_spend.target_spend_micros = 0
        elif bidding == 'MAXIMIZE_CONVERSIONS':
            campaign.maximize_conversions.target_cpa_micros = 0

        campaign.start_date = datetime.date.today().strftime('%Y%m%d')

        campaign_resp = campaign_service.mutate_campaigns(
            customer_id=customer_id, operations=[campaign_op])
        campaign_rn = campaign_resp.results[0].resource_name

        # Geo + language targeting
        if geo and geo in GEO_TARGETS:
            cs = client.get_service('CampaignCriterionService')
            op = client.get_type('CampaignCriterionOperation')
            op.create.campaign = campaign_rn
            op.create.location.geo_target_constant = GEO_TARGETS[geo]
            cs.mutate_campaign_criteria(customer_id=customer_id, operations=[op])
        if language and language in LANGUAGE_TARGETS:
            cs = client.get_service('CampaignCriterionService')
            op = client.get_type('CampaignCriterionOperation')
            op.create.campaign = campaign_rn
            op.create.language.language_constant = LANGUAGE_TARGETS[language]
            cs.mutate_campaign_criteria(customer_id=customer_id, operations=[op])

        return _ok(campaign_id=_resource_id(campaign_rn),
                    budget_id=_resource_id(budget_rn),
                    resource_name=campaign_rn)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def create_shopping_campaign(name, budget_thb, merchant_id, customer_id=None,
                             priority=0, geo='Thailand', language='th'):
    """Create a Shopping campaign. Requires Google Merchant Center ID."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        budget_service = client.get_service('CampaignBudgetService')
        budget_op = client.get_type('CampaignBudgetOperation')
        budget = budget_op.create
        budget.name = f'{name} Budget {uuid.uuid4().hex[:8]}'
        budget.amount_micros = _thb_to_micros(budget_thb)
        budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget_resp = budget_service.mutate_campaign_budgets(
            customer_id=customer_id, operations=[budget_op])
        budget_rn = budget_resp.results[0].resource_name

        campaign_service = client.get_service('CampaignService')
        campaign_op = client.get_type('CampaignOperation')
        campaign = campaign_op.create
        campaign.name = name
        campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SHOPPING
        campaign.status = client.enums.CampaignStatusEnum.PAUSED
        campaign.campaign_budget = budget_rn
        campaign.manual_cpc.enhanced_cpc_enabled = False

        campaign.shopping_setting.merchant_id = int(merchant_id)
        campaign.shopping_setting.campaign_priority = priority
        campaign.shopping_setting.enable_local = True

        campaign.start_date = datetime.date.today().strftime('%Y%m%d')

        campaign_resp = campaign_service.mutate_campaigns(
            customer_id=customer_id, operations=[campaign_op])
        campaign_rn = campaign_resp.results[0].resource_name

        if geo and geo in GEO_TARGETS:
            cs = client.get_service('CampaignCriterionService')
            op = client.get_type('CampaignCriterionOperation')
            op.create.campaign = campaign_rn
            op.create.location.geo_target_constant = GEO_TARGETS[geo]
            cs.mutate_campaign_criteria(customer_id=customer_id, operations=[op])

        return _ok(campaign_id=_resource_id(campaign_rn),
                    budget_id=_resource_id(budget_rn),
                    resource_name=campaign_rn)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def create_pmax_campaign(name, budget_thb, customer_id=None,
                         final_url=None, geo='Thailand', language='th'):
    """Create a Performance Max campaign. Use create_asset_group() next."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        budget_service = client.get_service('CampaignBudgetService')
        budget_op = client.get_type('CampaignBudgetOperation')
        budget = budget_op.create
        budget.name = f'{name} Budget {uuid.uuid4().hex[:8]}'
        budget.amount_micros = _thb_to_micros(budget_thb)
        budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget_resp = budget_service.mutate_campaign_budgets(
            customer_id=customer_id, operations=[budget_op])
        budget_rn = budget_resp.results[0].resource_name

        campaign_service = client.get_service('CampaignService')
        campaign_op = client.get_type('CampaignOperation')
        campaign = campaign_op.create
        campaign.name = name
        campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
        campaign.status = client.enums.CampaignStatusEnum.PAUSED
        campaign.campaign_budget = budget_rn

        campaign.maximize_conversion_value.target_roas = 0
        campaign.url_expansion_opt_out = False

        campaign.start_date = datetime.date.today().strftime('%Y%m%d')

        campaign_resp = campaign_service.mutate_campaigns(
            customer_id=customer_id, operations=[campaign_op])
        campaign_rn = campaign_resp.results[0].resource_name

        if geo and geo in GEO_TARGETS:
            cs = client.get_service('CampaignCriterionService')
            op = client.get_type('CampaignCriterionOperation')
            op.create.campaign = campaign_rn
            op.create.location.geo_target_constant = GEO_TARGETS[geo]
            cs.mutate_campaign_criteria(customer_id=customer_id, operations=[op])
        if language and language in LANGUAGE_TARGETS:
            cs = client.get_service('CampaignCriterionService')
            op = client.get_type('CampaignCriterionOperation')
            op.create.campaign = campaign_rn
            op.create.language.language_constant = LANGUAGE_TARGETS[language]
            cs.mutate_campaign_criteria(customer_id=customer_id, operations=[op])

        return _ok(campaign_id=_resource_id(campaign_rn),
                    budget_id=_resource_id(budget_rn),
                    resource_name=campaign_rn,
                    next_step='Use create_asset_group() to add assets')

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def list_campaigns(customer_id=None, days=30, status_filter=None):
    """List campaigns with performance metrics."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ga = client.get_service('GoogleAdsService')
        where = ''
        if status_filter:
            where = f"AND campaign.status = '{status_filter}'"
        query = f'''
            SELECT campaign.id, campaign.name, campaign.status,
                   campaign.advertising_channel_type,
                   campaign_budget.amount_micros,
                   metrics.impressions, metrics.clicks,
                   metrics.cost_micros, metrics.ctr, metrics.conversions
            FROM campaign
            WHERE segments.date DURING LAST_{days}_DAYS
            {where}
            ORDER BY metrics.cost_micros DESC
            LIMIT 50
        '''
        response = ga.search(customer_id=customer_id, query=query)
        results = []
        for row in response:
            results.append({
                'campaign_id': str(row.campaign.id),
                'name': row.campaign.name,
                'status': row.campaign.status.name,
                'type': row.campaign.advertising_channel_type.name,
                'budget_thb': _micros_to_thb(row.campaign_budget.amount_micros),
                'impressions': row.metrics.impressions,
                'clicks': row.metrics.clicks,
                'cost_thb': _micros_to_thb(row.metrics.cost_micros),
                'ctr': round(row.metrics.ctr * 100, 2),
                'conversions': round(row.metrics.conversions, 1),
            })
        return results

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def pause_campaign(campaign_id, customer_id=None):
    """Pause a campaign."""
    return _set_campaign_status(campaign_id, 'PAUSED', customer_id)


def enable_campaign(campaign_id, customer_id=None):
    """Enable a campaign."""
    return _set_campaign_status(campaign_id, 'ENABLED', customer_id)


def _set_campaign_status(campaign_id, status, customer_id=None):
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        campaign_service = client.get_service('CampaignService')
        campaign_op = client.get_type('CampaignOperation')
        campaign = campaign_op.update
        campaign.resource_name = campaign_service.campaign_path(customer_id, campaign_id)
        campaign.status = getattr(client.enums.CampaignStatusEnum, status)
        _set_field_mask(client, campaign_op, ['status'])
        campaign_service.mutate_campaigns(
            customer_id=customer_id, operations=[campaign_op])
        return _ok(campaign_id=str(campaign_id), status=status)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def update_campaign_budget(campaign_id, new_budget_thb, customer_id=None):
    """Update a campaign's daily budget."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ga = client.get_service('GoogleAdsService')
        # Find the budget resource name
        query = f'''
            SELECT campaign.campaign_budget
            FROM campaign
            WHERE campaign.id = {campaign_id}
        '''
        response = ga.search(customer_id=customer_id, query=query)
        budget_rn = None
        for row in response:
            budget_rn = row.campaign.campaign_budget
            break
        if not budget_rn:
            return _fail(f'Campaign {campaign_id} not found')

        budget_service = client.get_service('CampaignBudgetService')
        budget_op = client.get_type('CampaignBudgetOperation')
        budget = budget_op.update
        budget.resource_name = budget_rn
        budget.amount_micros = _thb_to_micros(new_budget_thb)
        _set_field_mask(client, budget_op, ['amount_micros'])
        budget_service.mutate_campaign_budgets(
            customer_id=customer_id, operations=[budget_op])
        return _ok(campaign_id=str(campaign_id), new_budget_thb=new_budget_thb)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def delete_campaign(campaign_id, customer_id=None):
    """Remove (delete) a campaign."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        campaign_service = client.get_service('CampaignService')
        campaign_op = client.get_type('CampaignOperation')
        campaign_op.remove = campaign_service.campaign_path(customer_id, campaign_id)
        campaign_service.mutate_campaigns(
            customer_id=customer_id, operations=[campaign_op])
        return _ok(campaign_id=str(campaign_id), deleted=True)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


# ============================================================
# Ad Group Operations
# ============================================================

def create_ad_group(campaign_id, name, cpc_bid_thb=5.0, customer_id=None):
    """Create an ad group in a campaign. Not for PMax - use create_asset_group instead."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()

        # Check campaign type - PMax uses asset groups
        ga = client.get_service('GoogleAdsService')
        query = f'''
            SELECT campaign.advertising_channel_type
            FROM campaign WHERE campaign.id = {campaign_id}
        '''
        response = ga.search(customer_id=customer_id, query=query)
        for row in response:
            if row.campaign.advertising_channel_type.name == 'PERFORMANCE_MAX':
                return _fail('PMax campaigns use asset groups, not ad groups. Use create_asset_group() instead.', 'INVALID_OPERATION')

        campaign_service = client.get_service('CampaignService')
        ad_group_service = client.get_service('AdGroupService')
        ad_group_op = client.get_type('AdGroupOperation')
        ad_group = ad_group_op.create
        ad_group.name = name
        ad_group.status = client.enums.AdGroupStatusEnum.ENABLED
        ad_group.campaign = campaign_service.campaign_path(customer_id, campaign_id)
        ad_group.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
        ad_group.cpc_bid_micros = _thb_to_micros(cpc_bid_thb)

        response = ad_group_service.mutate_ad_groups(
            customer_id=customer_id, operations=[ad_group_op])
        ad_group_rn = response.results[0].resource_name
        return _ok(ad_group_id=_resource_id(ad_group_rn), resource_name=ad_group_rn)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def list_ad_groups(campaign_id, customer_id=None):
    """List ad groups in a campaign."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ga = client.get_service('GoogleAdsService')
        query = f'''
            SELECT ad_group.id, ad_group.name, ad_group.status,
                   ad_group.cpc_bid_micros,
                   metrics.impressions, metrics.clicks, metrics.cost_micros
            FROM ad_group
            WHERE campaign.id = {campaign_id}
            ORDER BY ad_group.name
        '''
        response = ga.search(customer_id=customer_id, query=query)
        results = []
        for row in response:
            results.append({
                'ad_group_id': str(row.ad_group.id),
                'name': row.ad_group.name,
                'status': row.ad_group.status.name,
                'cpc_bid_thb': _micros_to_thb(row.ad_group.cpc_bid_micros),
                'impressions': row.metrics.impressions,
                'clicks': row.metrics.clicks,
                'cost_thb': _micros_to_thb(row.metrics.cost_micros),
            })
        return results

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def pause_ad_group(ad_group_id, customer_id=None):
    return _set_ad_group_status(ad_group_id, 'PAUSED', customer_id)


def enable_ad_group(ad_group_id, customer_id=None):
    return _set_ad_group_status(ad_group_id, 'ENABLED', customer_id)


def _set_ad_group_status(ad_group_id, status, customer_id=None):
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ad_group_service = client.get_service('AdGroupService')
        op = client.get_type('AdGroupOperation')
        ag = op.update
        ag.resource_name = ad_group_service.ad_group_path(customer_id, ad_group_id)
        ag.status = getattr(client.enums.AdGroupStatusEnum, status)
        _set_field_mask(client, op, ['status'])
        ad_group_service.mutate_ad_groups(customer_id=customer_id, operations=[op])
        return _ok(ad_group_id=str(ad_group_id), status=status)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def set_ad_group_bid(ad_group_id, cpc_bid_thb, customer_id=None):
    """Update CPC bid for an ad group."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ad_group_service = client.get_service('AdGroupService')
        op = client.get_type('AdGroupOperation')
        ag = op.update
        ag.resource_name = ad_group_service.ad_group_path(customer_id, ad_group_id)
        ag.cpc_bid_micros = _thb_to_micros(cpc_bid_thb)
        _set_field_mask(client, op, ['cpc_bid_micros'])
        ad_group_service.mutate_ad_groups(customer_id=customer_id, operations=[op])
        return _ok(ad_group_id=str(ad_group_id), cpc_bid_thb=cpc_bid_thb)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


# ============================================================
# Ad Operations
# ============================================================

def create_rsa(ad_group_id, headlines, descriptions, final_url,
               customer_id=None, path1='', path2='', status='PAUSED'):
    """Create a Responsive Search Ad.
    headlines: list of 3-15 strings (max 30 chars each)
    descriptions: list of 2-4 strings (max 90 chars each)
    """
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ad_group_ad_service = client.get_service('AdGroupAdService')
        ad_group_service = client.get_service('AdGroupService')

        op = client.get_type('AdGroupAdOperation')
        ad_group_ad = op.create
        ad_group_ad.ad_group = ad_group_service.ad_group_path(customer_id, ad_group_id)
        ad_group_ad.status = getattr(client.enums.AdGroupAdStatusEnum, status)

        ad = ad_group_ad.ad
        ad.final_urls.append(final_url)

        for h in headlines:
            asset = client.get_type('AdTextAsset')
            asset.text = h
            ad.responsive_search_ad.headlines.append(asset)

        for d in descriptions:
            asset = client.get_type('AdTextAsset')
            asset.text = d
            ad.responsive_search_ad.descriptions.append(asset)

        if path1:
            ad.responsive_search_ad.path1 = path1
        if path2:
            ad.responsive_search_ad.path2 = path2

        response = ad_group_ad_service.mutate_ad_group_ads(
            customer_id=customer_id, operations=[op])
        rn = response.results[0].resource_name
        return _ok(ad_id=_resource_id(rn), resource_name=rn)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def create_display_ad(ad_group_id, headlines, descriptions, business_name,
                      final_url, marketing_image_url, customer_id=None,
                      long_headline=''):
    """Create a Responsive Display Ad. Uploads image asset first."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        import urllib.request

        # 1. Download and upload image as asset
        asset_service = client.get_service('AssetService')
        asset_op = client.get_type('AssetOperation')
        asset = asset_op.create
        asset.type_ = client.enums.AssetTypeEnum.IMAGE
        asset.name = f'Display image {uuid.uuid4().hex[:8]}'

        req = urllib.request.Request(marketing_image_url,
                                     headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as resp:
            asset.image_asset.data = resp.read()

        asset_resp = asset_service.mutate_assets(
            customer_id=customer_id, operations=[asset_op])
        image_asset_rn = asset_resp.results[0].resource_name

        # 2. Create responsive display ad
        ad_group_ad_service = client.get_service('AdGroupAdService')
        ad_group_service = client.get_service('AdGroupService')

        op = client.get_type('AdGroupAdOperation')
        ad_group_ad = op.create
        ad_group_ad.ad_group = ad_group_service.ad_group_path(customer_id, ad_group_id)
        ad_group_ad.status = client.enums.AdGroupAdStatusEnum.PAUSED

        ad = ad_group_ad.ad
        ad.final_urls.append(final_url)

        rda = ad.responsive_display_ad

        for h in headlines:
            asset_item = client.get_type('AdTextAsset')
            asset_item.text = h
            rda.headlines.append(asset_item)

        for d in descriptions:
            asset_item = client.get_type('AdTextAsset')
            asset_item.text = d
            rda.descriptions.append(asset_item)

        rda.business_name = business_name
        if long_headline:
            lh_asset = client.get_type('AdTextAsset')
            lh_asset.text = long_headline
            rda.long_headline = lh_asset

        img_asset = client.get_type('AdImageAsset')
        img_asset.asset = image_asset_rn
        rda.marketing_images.append(img_asset)

        response = ad_group_ad_service.mutate_ad_group_ads(
            customer_id=customer_id, operations=[op])
        rn = response.results[0].resource_name
        return _ok(ad_id=_resource_id(rn), resource_name=rn,
                    image_asset=image_asset_rn)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def list_ads(ad_group_id, customer_id=None):
    """List ads in an ad group."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ga = client.get_service('GoogleAdsService')
        query = f'''
            SELECT ad_group_ad.ad.id, ad_group_ad.ad.name,
                   ad_group_ad.ad.type, ad_group_ad.status,
                   ad_group_ad.ad.final_urls,
                   metrics.impressions, metrics.clicks, metrics.cost_micros
            FROM ad_group_ad
            WHERE ad_group.id = {ad_group_id}
        '''
        response = ga.search(customer_id=customer_id, query=query)
        results = []
        for row in response:
            results.append({
                'ad_id': str(row.ad_group_ad.ad.id),
                'name': row.ad_group_ad.ad.name,
                'type': row.ad_group_ad.ad.type_.name,
                'status': row.ad_group_ad.status.name,
                'final_urls': list(row.ad_group_ad.ad.final_urls),
                'impressions': row.metrics.impressions,
                'clicks': row.metrics.clicks,
                'cost_thb': _micros_to_thb(row.metrics.cost_micros),
            })
        return results

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def pause_ad(ad_id, ad_group_id, customer_id=None):
    """Pause an ad."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ad_group_ad_service = client.get_service('AdGroupAdService')
        op = client.get_type('AdGroupAdOperation')
        aga = op.update
        aga.resource_name = ad_group_ad_service.ad_group_ad_path(
            customer_id, ad_group_id, ad_id)
        aga.status = client.enums.AdGroupAdStatusEnum.PAUSED
        _set_field_mask(client, op, ['status'])
        ad_group_ad_service.mutate_ad_group_ads(
            customer_id=customer_id, operations=[op])
        return _ok(ad_id=str(ad_id), status='PAUSED')

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


# ============================================================
# Keyword Operations
# ============================================================

def add_keywords(ad_group_id, keywords, match_type='BROAD',
                 cpc_bid_thb=None, customer_id=None):
    """Add keywords to an ad group.
    keywords: list of keyword strings
    match_type: 'BROAD', 'PHRASE', or 'EXACT'
    """
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ad_group_service = client.get_service('AdGroupService')
        criterion_service = client.get_service('AdGroupCriterionService')

        ad_group_rn = ad_group_service.ad_group_path(customer_id, ad_group_id)
        mt = getattr(client.enums.KeywordMatchTypeEnum, match_type)

        operations = []
        for kw in keywords:
            op = client.get_type('AdGroupCriterionOperation')
            criterion = op.create
            criterion.ad_group = ad_group_rn
            criterion.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
            criterion.keyword.text = kw
            criterion.keyword.match_type = mt
            if cpc_bid_thb:
                criterion.cpc_bid_micros = _thb_to_micros(cpc_bid_thb)
            operations.append(op)

        response = criterion_service.mutate_ad_group_criteria(
            customer_id=customer_id, operations=operations)
        criterion_ids = [_resource_id(r.resource_name) for r in response.results]
        return _ok(added=len(criterion_ids), criterion_ids=criterion_ids)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def list_keywords(ad_group_id, customer_id=None):
    """List keywords in an ad group."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ga = client.get_service('GoogleAdsService')
        query = f'''
            SELECT ad_group_criterion.criterion_id,
                   ad_group_criterion.keyword.text,
                   ad_group_criterion.keyword.match_type,
                   ad_group_criterion.status,
                   ad_group_criterion.cpc_bid_micros,
                   metrics.impressions, metrics.clicks, metrics.cost_micros
            FROM keyword_view
            WHERE ad_group.id = {ad_group_id}
        '''
        response = ga.search(customer_id=customer_id, query=query)
        results = []
        for row in response:
            results.append({
                'criterion_id': str(row.ad_group_criterion.criterion_id),
                'keyword': row.ad_group_criterion.keyword.text,
                'match_type': row.ad_group_criterion.keyword.match_type.name,
                'status': row.ad_group_criterion.status.name,
                'cpc_bid_thb': _micros_to_thb(row.ad_group_criterion.cpc_bid_micros),
                'impressions': row.metrics.impressions,
                'clicks': row.metrics.clicks,
                'cost_thb': _micros_to_thb(row.metrics.cost_micros),
            })
        return results

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def remove_keyword(criterion_id, ad_group_id, customer_id=None):
    """Remove a keyword from an ad group."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        criterion_service = client.get_service('AdGroupCriterionService')
        op = client.get_type('AdGroupCriterionOperation')
        op.remove = criterion_service.ad_group_criterion_path(
            customer_id, ad_group_id, criterion_id)
        criterion_service.mutate_ad_group_criteria(
            customer_id=customer_id, operations=[op])
        return _ok(criterion_id=str(criterion_id), removed=True)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


# ============================================================
# Performance Max - Asset Groups
# ============================================================

def create_asset_group(campaign_id, name, final_url, customer_id=None,
                       path1='', path2=''):
    """Create an asset group for a PMax campaign."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        campaign_service = client.get_service('CampaignService')
        asset_group_service = client.get_service('AssetGroupService')

        op = client.get_type('AssetGroupOperation')
        ag = op.create
        ag.name = name
        ag.campaign = campaign_service.campaign_path(customer_id, campaign_id)
        ag.final_urls.append(final_url)
        ag.status = client.enums.AssetGroupStatusEnum.PAUSED
        if path1:
            ag.path1 = path1
        if path2:
            ag.path2 = path2

        response = asset_group_service.mutate_asset_groups(
            customer_id=customer_id, operations=[op])
        rn = response.results[0].resource_name
        return _ok(asset_group_id=_resource_id(rn), resource_name=rn)

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def add_pmax_assets(asset_group_id, assets, customer_id=None):
    """Add text and image assets to a PMax asset group.
    assets dict format:
    {
        'headlines': ['text1', 'text2', ...],       # 3-5 items
        'descriptions': ['desc1', ...],             # 2-5 items
        'long_headline': 'text',
        'business_name': 'My Business',
        'image_urls': ['https://...'],              # marketing images
    }
    """
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        asset_service = client.get_service('AssetService')
        asset_group_asset_service = client.get_service('AssetGroupAssetService')

        asset_group_rn = f'customers/{customer_id}/assetGroups/{asset_group_id}'
        created_links = []

        # Helper to create a text asset and link it
        def _create_text_asset_and_link(text, field_type):
            # Create asset
            asset_op = client.get_type('AssetOperation')
            asset_op.create.text_asset.text = text
            asset_op.create.type_ = client.enums.AssetTypeEnum.TEXT
            asset_op.create.name = f'{field_type}_{uuid.uuid4().hex[:6]}'
            resp = asset_service.mutate_assets(
                customer_id=customer_id, operations=[asset_op])
            asset_rn = resp.results[0].resource_name

            # Link to asset group
            link_op = client.get_type('AssetGroupAssetOperation')
            link = link_op.create
            link.asset = asset_rn
            link.asset_group = asset_group_rn
            link.field_type = getattr(client.enums.AssetFieldTypeEnum, field_type)
            asset_group_asset_service.mutate_asset_group_assets(
                customer_id=customer_id, operations=[link_op])
            created_links.append(asset_rn)

        # Headlines
        for h in assets.get('headlines', []):
            _create_text_asset_and_link(h, 'HEADLINE')

        # Descriptions
        for d in assets.get('descriptions', []):
            _create_text_asset_and_link(d, 'DESCRIPTION')

        # Long headline
        if assets.get('long_headline'):
            _create_text_asset_and_link(assets['long_headline'], 'LONG_HEADLINE')

        # Business name
        if assets.get('business_name'):
            _create_text_asset_and_link(assets['business_name'], 'BUSINESS_NAME')

        # Image assets
        import urllib.request
        for img_url in assets.get('image_urls', []):
            asset_op = client.get_type('AssetOperation')
            asset_op.create.type_ = client.enums.AssetTypeEnum.IMAGE
            asset_op.create.name = f'pmax_img_{uuid.uuid4().hex[:6]}'
            req = urllib.request.Request(img_url,
                                         headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as resp:
                asset_op.create.image_asset.data = resp.read()
            resp = asset_service.mutate_assets(
                customer_id=customer_id, operations=[asset_op])
            img_rn = resp.results[0].resource_name

            link_op = client.get_type('AssetGroupAssetOperation')
            link = link_op.create
            link.asset = img_rn
            link.asset_group = asset_group_rn
            link.field_type = client.enums.AssetFieldTypeEnum.MARKETING_IMAGE
            asset_group_asset_service.mutate_asset_group_assets(
                customer_id=customer_id, operations=[link_op])
            created_links.append(img_rn)

        return _ok(assets_linked=len(created_links))

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


# ============================================================
# Reporting
# ============================================================

def campaign_performance(customer_id=None, days=30):
    """Get campaign performance report."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ga = client.get_service('GoogleAdsService')
        query = f'''
            SELECT campaign.id, campaign.name, campaign.status,
                   campaign.advertising_channel_type,
                   campaign_budget.amount_micros,
                   metrics.impressions, metrics.clicks,
                   metrics.cost_micros, metrics.ctr,
                   metrics.average_cpc, metrics.conversions,
                   metrics.conversions_value
            FROM campaign
            WHERE segments.date DURING LAST_{days}_DAYS
                AND campaign.status != 'REMOVED'
            ORDER BY metrics.cost_micros DESC
        '''
        response = ga.search(customer_id=customer_id, query=query)
        results = []
        for row in response:
            results.append({
                'campaign_id': str(row.campaign.id),
                'name': row.campaign.name,
                'status': row.campaign.status.name,
                'type': row.campaign.advertising_channel_type.name,
                'budget_thb': _micros_to_thb(row.campaign_budget.amount_micros),
                'impressions': row.metrics.impressions,
                'clicks': row.metrics.clicks,
                'cost_thb': _micros_to_thb(row.metrics.cost_micros),
                'ctr': round(row.metrics.ctr * 100, 2),
                'avg_cpc_thb': _micros_to_thb(row.metrics.average_cpc),
                'conversions': round(row.metrics.conversions, 1),
                'conversion_value': round(row.metrics.conversions_value, 2),
            })
        return results

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def ad_group_performance(campaign_id, customer_id=None, days=30):
    """Get ad group performance for a campaign."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ga = client.get_service('GoogleAdsService')
        query = f'''
            SELECT ad_group.id, ad_group.name, ad_group.status,
                   ad_group.cpc_bid_micros,
                   metrics.impressions, metrics.clicks,
                   metrics.cost_micros, metrics.ctr,
                   metrics.average_cpc, metrics.conversions
            FROM ad_group
            WHERE campaign.id = {campaign_id}
                AND segments.date DURING LAST_{days}_DAYS
            ORDER BY metrics.cost_micros DESC
        '''
        response = ga.search(customer_id=customer_id, query=query)
        results = []
        for row in response:
            results.append({
                'ad_group_id': str(row.ad_group.id),
                'name': row.ad_group.name,
                'status': row.ad_group.status.name,
                'cpc_bid_thb': _micros_to_thb(row.ad_group.cpc_bid_micros),
                'impressions': row.metrics.impressions,
                'clicks': row.metrics.clicks,
                'cost_thb': _micros_to_thb(row.metrics.cost_micros),
                'ctr': round(row.metrics.ctr * 100, 2),
                'avg_cpc_thb': _micros_to_thb(row.metrics.average_cpc),
                'conversions': round(row.metrics.conversions, 1),
            })
        return results

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


def keyword_performance(campaign_id, customer_id=None, days=30):
    """Get keyword performance for a campaign."""
    customer_id = customer_id or _get_customer_id()
    try:
        client = _get_client()
        ga = client.get_service('GoogleAdsService')
        query = f'''
            SELECT ad_group_criterion.criterion_id,
                   ad_group_criterion.keyword.text,
                   ad_group_criterion.keyword.match_type,
                   ad_group.name,
                   metrics.impressions, metrics.clicks,
                   metrics.cost_micros, metrics.ctr,
                   metrics.average_cpc, metrics.conversions
            FROM keyword_view
            WHERE campaign.id = {campaign_id}
                AND segments.date DURING LAST_{days}_DAYS
            ORDER BY metrics.cost_micros DESC
        '''
        response = ga.search(customer_id=customer_id, query=query)
        results = []
        for row in response:
            results.append({
                'criterion_id': str(row.ad_group_criterion.criterion_id),
                'keyword': row.ad_group_criterion.keyword.text,
                'match_type': row.ad_group_criterion.keyword.match_type.name,
                'ad_group': row.ad_group.name,
                'impressions': row.metrics.impressions,
                'clicks': row.metrics.clicks,
                'cost_thb': _micros_to_thb(row.metrics.cost_micros),
                'ctr': round(row.metrics.ctr * 100, 2),
                'avg_cpc_thb': _micros_to_thb(row.metrics.average_cpc),
                'conversions': round(row.metrics.conversions, 1),
            })
        return results

    except GoogleAdsException as e:
        return _handle_ads_error(e)
    except Exception as e:
        return _fail(str(e))


# ============================================================
# CLI Entry Point
# ============================================================

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 gads.py <function> [args_json]')
        print('Example: python3 gads.py list_campaigns \'{"days": 7}\'')
        print('\nAvailable functions:')
        _skip = {'AdsError', 'GoogleAdsClient', 'GoogleAdsException'}
        funcs = [name for name in dir() if not name.startswith('_')
                 and callable(eval(name)) and name not in _skip]
        for f in sorted(funcs):
            print(f'  {f}')
        sys.exit(0)

    fn_name = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    fn = globals().get(fn_name)
    if not fn or not callable(fn) or fn_name.startswith('_'):
        print(f'Unknown function: {fn_name}')
        sys.exit(1)

    result = fn(**args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
