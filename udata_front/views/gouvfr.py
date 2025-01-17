import frontmatter
import logging
import requests

from flask import url_for, redirect, abort, current_app, g
from jinja2.exceptions import TemplateNotFound
from mongoengine.errors import ValidationError

from udata_front import theme
from udata_front.theme import theme_static_with_version
from udata.app import cache
from udata.frontend import template_hook
from udata.models import Reuse, Dataset
from udata.i18n import I18nBlueprint

from udata_front import APIGOUVFR_EXTRAS_KEY

log = logging.getLogger(__name__)

blueprint = I18nBlueprint(
    "gouvfr",
    __name__,
    template_folder="../templates",
    static_folder="../static",
    static_url_path="/static/gouvfr",
)

PAGE_CACHE_DURATION = 60 * 5  # in seconds


@blueprint.route("/dataset/<dataset>/")
def redirect_datasets(dataset):
    """Route Legacy CKAN datasets"""
    return redirect(url_for("datasets.show", dataset=dataset))


@blueprint.route("/organization/")
def redirect_organizations_list():
    """Route legacy CKAN organizations listing"""
    return redirect(url_for("organizations.list"))


@blueprint.route("/organization/<org>/")
def redirect_organizations(org):
    """Route legacy CKAN organizations"""
    return redirect(url_for("organizations.show", org=org))


@blueprint.route("/group/<topic>/")
def redirect_topics(topic):
    """Route legacy CKAN topics"""
    return redirect(url_for("topics.display", topic=topic))


def get_pages_gh_urls(slug, locale:str = None):
    repo = current_app.config.get("PAGES_GH_REPO_NAME")
    if not repo:
        abort(404)
    branch = current_app.config.get("PAGES_REPO_BRANCH", "master")
    if locale:
        raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/pages/{locale}/{slug}"
        gh_url = f"https://github.com/{repo}/blob/{branch}/pages/{slug}"
    else:
        raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/pages/{slug}"
        gh_url = f"https://github.com/{repo}/blob/{branch}/pages/{slug}"
    return raw_url, gh_url


def detect_pages_extension(raw_url):
    if requests.head(f"{raw_url}.md").status_code == 200:
        return "md"
    return "html"


def get_page_content(slug):
    content = None
    for locale in [g.lang_code, None]:
        content, gh_url, extension = get_page_content_locale(slug, locale)
        if content:
            break
    else:    # no cached version or no content from gh
        log.error(f"No content found inc. from cache for page {slug}")
        abort(404)
    return content, gh_url, extension


@cache.memoize(PAGE_CACHE_DURATION)
def get_page_content_locale(slug, locale):
    """
    Get a page content from gh repo (md).
    This has a double layer of cache:
    - @cache.cached decorator w/ short lived cache for normal operations
    - a long terme cache w/o timeout to be able to always render some content
    """
    cache_key = "pages-content-{slug}-{locale}".format(
        slug=slug,
        locale="default" if locale is None else locale
    )
    raw_url, gh_url = get_pages_gh_urls(slug, locale=locale)
    try:
        extension = detect_pages_extension(raw_url)

        raw_url = f"{raw_url}.{extension}"
        gh_url = f"{gh_url}.{extension}"

        response = requests.get(raw_url, timeout=5)
        # do not cache 404 and forward status code
        if response.status_code == 404:
            return None, gh_url, extension
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        log.exception(f"Error while getting {slug} page from gh: {e}")
        content = cache.get(cache_key)
    else:
        content = response.text
        cache.set(cache_key, content)
    return content, gh_url, extension


def get_object(model, id_or_slug):
    objects = getattr(model, "objects")
    obj = objects.filter(slug=id_or_slug).first()
    if not obj:
        try:
            obj = objects.filter(id=id_or_slug).first()
        except ValidationError:
            pass
    return obj


def get_objects_from_tags(model, tags:list):
    return list(getattr(model, "objects").visible().filter(tags=tags).order_by('-created_at'))


@blueprint.route("/pages/<path:slug>/")
def show_page(slug):
    content, gh_url, extension = get_page_content(slug)
    page = frontmatter.loads(content)

    models = {"reuses": Reuse, "datasets": Dataset}
    data = {"reuses": [], "datasets": []}

    for model_key, model in models.items():
        tags = []
        for r in page.get(model_key) or []:
            if r is None:
                continue
            r = r.strip()
            if r[:4] == "tag#":
                tags.append(r[:4])
            else:
                res = get_object(model, r)
                if res:
                    data[model_key].append(res)
        if len(tags) > 0:
            data[model_key] += get_objects_from_tags(model, tags)

    return theme.render(
        "page.html",
        page=page,
        reuses=data["reuses"],
        datasets=data["datasets"],
        gh_url=gh_url,
        extension=extension,
    )


@blueprint.route("/suivi/")
def suivi():
    try:
        return theme.render("suivi.html")
    except TemplateNotFound:
        abort(404)


def has_apis(ctx):
    dataset = ctx["dataset"]
    return dataset.extras.get(APIGOUVFR_EXTRAS_KEY, [])


@template_hook("dataset.display.after-description", when=has_apis)
def dataset_apis(ctx):
    dataset = ctx["dataset"]
    return theme.render(
        "dataset-apis.html", apis=dataset.extras.get(APIGOUVFR_EXTRAS_KEY)
    )


@template_hook("oauth_authorize_theme_content")
def oauth_authorize_theme_content(ctx):
    grant = ctx["grant"]
    return theme.render("api/oauth_authorize.html", grant=grant)


@template_hook("oauth_error_theme_content")
def oauth_error_theme_content(ctx):
    request = ctx["request"]
    return theme.render("api/oauth_error.html", error=request.args.get("error"))


# TODO : better this, redirect is not the best. How to serve it instead ?!
@blueprint.route("/_stylemark/<path:filename>/")
def stylemark(filename):
    return redirect(theme_static_with_version(None, filename="stylemark/index.html"))
