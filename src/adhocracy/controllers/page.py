import json
import logging
from operator import itemgetter


import formencode
from formencode import htmlfill, Invalid, validators

from pylons import request, tmpl_context as c
from pylons.controllers.util import abort, redirect
from pylons.decorators import validate
from pylons.i18n import _

from adhocracy import config
from adhocracy import forms, model
from adhocracy.lib import democracy, event, helpers as h
from adhocracy.lib import pager, sorting, tiles, watchlist, logo
from adhocracy.lib.auth import guard
from adhocracy.lib.auth import can, require
from adhocracy.lib.auth.csrf import RequireInternalRequest
from adhocracy.lib.base import BaseController
from adhocracy.lib.instance import RequireInstance
from adhocracy.lib.staticpage import add_static_content
from adhocracy.lib.templating import (render, render_json, ret_abort,
                                      render_logo)
from adhocracy.lib.templating import OVERLAY_SMALL
from adhocracy.lib.text.diff import (norm_texts_inline_compare,
                                     page_titles_compare)
from adhocracy.lib.text.render import render_line_based, render as render_text
import adhocracy.lib.text as libtext
from adhocracy.lib.util import get_entity_or_abort


log = logging.getLogger(__name__)


class NoneObject(object):
    pass

NoPage = NoneObject()


class PageCreateForm(formencode.Schema):
    allow_extra_fields = True
    title = forms.ValidTitle(unused_label=True)
    text = validators.String(max=20000, min=0, not_empty=False,
                             if_empty=None, if_missing=None)
    parent = forms.ValidPage(if_missing=None, if_empty=None, not_empty=False)
    proposal = forms.ValidProposal(not_empty=False, if_empty=None,
                                   if_missing=None)
    tags = validators.String(max=20000, not_empty=False)
    milestone = forms.MaybeMilestone(if_empty=None, if_missing=None)
    category = formencode.foreach.ForEach(forms.ValidCategoryBadge())
    formatting = validators.StringBool(not_empty=False, if_empty=False,
                                       if_missing=False)
    container = validators.StringBool(not_empty=False, if_empty=False,
                                      if_missing=False)
    sectionpage = validators.StringBool(not_empty=False, if_empty=False,
                                        if_missing=False)
    allow_comment = validators.StringBool(not_empty=False, if_empty=False,
                                          if_missing=False)
    allow_selection = validators.StringBool(not_empty=False, if_empty=False,
                                            if_missing=False)
    always_show_original = validators.StringBool(not_empty=False,
                                                 if_empty=False,
                                                 if_missing=False)
    watch = validators.StringBool(not_empty=False, if_empty=False,
                                  if_missing=False)
    if config.get_bool('adhocracy.page.allow_abstracts'):
        abstract = validators.String(max=255, not_empty=False, if_empty=None,
                                     if_missing=None)


class PageEditForm(formencode.Schema):
    allow_extra_fields = True


class PageUpdateForm(formencode.Schema):
    allow_extra_fields = True
    title = forms.ValidTitle()
    variant = forms.VariantName(not_empty=True)
    text = validators.String(max=20000, min=0, not_empty=False,
                             if_empty=None, if_missing=None)
    parent_text = forms.ValidText(if_missing=None, if_empty=None,
                                  not_empty=False)
    parent_page = forms.ValidPage(if_missing=NoPage, if_empty=None,
                                  not_empty=False)
    proposal = forms.ValidProposal(not_empty=False, if_empty=None,
                                   if_missing=None)
    milestone = forms.MaybeMilestone(if_empty=None,
                                     if_missing=None)
    category = formencode.foreach.ForEach(forms.ValidCategoryBadge())
    formatting = validators.StringBool(not_empty=False, if_empty=False,
                                       if_missing=False)
    sectionpage = validators.StringBool(not_empty=False, if_empty=False,
                                        if_missing=False)
    allow_comment = validators.StringBool(not_empty=False, if_empty=False,
                                          if_missing=False)
    allow_selection = validators.StringBool(not_empty=False, if_empty=False,
                                            if_missing=False)
    always_show_original = validators.StringBool(not_empty=False,
                                                 if_empty=False,
                                                 if_missing=False)
    watch = validators.StringBool(not_empty=False, if_empty=False,
                                  if_missing=False)
    if config.get_bool('adhocracy.page.allow_abstracts'):
        abstract = validators.String(max=255, not_empty=False, if_empty=None,
                                     if_missing=None)


class PageFilterForm(formencode.Schema):
    allow_extra_fields = True
    pages_q = validators.String(max=255, not_empty=False, if_empty=u'',
                                if_missing=u'')


class PageDiffForm(formencode.Schema):
    allow_extra_fields = True
    left = forms.ValidText()
    right = forms.ValidText()


class PageController(BaseController):

    identifier = 'norms'

    @RequireInstance
    @guard.page.index()
    @validate(schema=PageFilterForm(), post_only=False, on_get=True)
    def index(self, format="html"):
        data = {}
        pages = model.Page.all(instance=c.instance,
                               functions=model.Page.LISTED)
        if request.params.get('pages_sort', '4') == '4':
            # crude hack to get only top level pages cause the pager
            # cannot handle this and we can not pass arguments to the tile
            # WARNING: This will break if the index of the sort changes.
            c.is_hierarchical = True
            pages = [page for page in pages if page.parent is None]

        data['pages_pager'] = pager.pages(pages)

        if format == 'json':
            return render_json(data['pages_pager'])

        tags = model.Tag.popular_tags(limit=30)
        data['cloud_tags'] = sorted(h.tag.tag_cloud_normalize(tags),
                                    key=lambda (k, c, v): k.name)
        data['tutorial_intro'] = _('tutorial_norms_overview_tab')
        data['tutorial'] = 'page_index'

        add_static_content(data, u'adhocracy.static.page_index_heading',
                           body_key=u'heading_text',
                           title_key=u'heading_title')

        if c.instance.page_index_as_tiles:
            return render("/page/index_tiles.html", data,
                          overlay=format == u'overlay',
                          overlay_size=OVERLAY_SMALL)
        else:
            return render("/page/index.html", data,
                          overlay=format == u'overlay',
                          overlay_size=OVERLAY_SMALL)

    @RequireInstance
    @guard.page.create()
    def new(self, errors=None, format=u'html'):
        defaults = dict(request.params)

        if not defaults:
            defaults['watch'] = True

        c.title = request.params.get('title', None)
        proposal_id = request.params.get("proposal")
        c.categories = model.CategoryBadge.all(
            c.instance, include_global=not c.instance.hide_global_categories)

        c.section = u'section_parent' in request.params
        if c.section:
            c.parent = get_entity_or_abort(
                model.Page, request.params.get(u'section_parent'))
            if c.title is None:
                c.title = u"%s %i" % (c.parent.label,
                                      len(c.parent.children))

        html = None
        if proposal_id is not None:
            c.proposal = model.Proposal.find(proposal_id)
            html = render('/selection/propose.html',
                          overlay=format == u'overlay')
        else:
            c.propose = None
            html = render("/page/new.html", overlay=format == u'overlay')

        return htmlfill.render(html, defaults=defaults, errors=errors,
                               force_defaults=False)

    @RequireInstance
    @RequireInternalRequest(methods=['POST'])
    @guard.page.create()
    def create(self, format='html'):
        try:
            self.form_result = PageCreateForm().to_python(request.params)
            # a proposal that this norm should be integrated with
            proposal = self.form_result.get("proposal")
            _text = self.form_result.get("text")
            if not can.norm.create():
                if not proposal:
                    msg = _("No proposal has been specified")
                    raise Invalid(msg, branch, state_(),
                                  error_dict={'title': msg})
                if not c.instance.allow_propose:
                    msg = _("You cannot create a new norm")
                    raise Invalid(msg, branch, state_(),
                                  error_dict={'title': msg})
                # if a proposal is specified, create a stub:
                _text = None
        except Invalid, i:
            return self.new(errors=i.unpack_errors())

        variant = self.form_result.get("title")
        container = self.form_result.get('container')
        page = model.Page.create(
            c.instance, variant, _text, c.user,
            function=(model.Page.CONTAINER if container else model.Page.NORM),
            formatting=(self.form_result.get("formatting")
                        or self.form_result.get("container")),
            sectionpage=(False if container
                         else self.form_result.get("sectionpage")),
            allow_comment=self.form_result.get("allow_comment"),
            allow_selection=self.form_result.get("allow_selection"),
            always_show_original=self.form_result.get("always_show_original"),
            tags=self.form_result.get("tags"))

        page.milestone = self.form_result.get('milestone')

        if self.form_result.get("parent") is not None:
            page.parents.append(self.form_result.get("parent"))

        if (config.get_bool('adhocracy.page.allow_abstracts')
                and c.instance.page_index_as_tiles
                and not page.is_section()):
            page.abstract = self.form_result.get('abstract')

        if c.came_from != u'':
            came_from = c.came_from
        elif proposal is not None and can.selection.create(proposal):
            model.Selection.create(proposal, page, c.user, variant=variant)
            # if a selection was created, go there instead:
            came_from = h.page.url(page, member='branch',
                                   query={'proposal': proposal.id})
        else:
            came_from = h.entity_url(page)  # by default, redirect to the page

        categories = self.form_result.get('category')
        category = categories[0] if categories else None
        page.set_category(category, c.user)

        model.meta.Session.commit()

        try:
            # fixme: show image errors in the form
            if ('logo' in request.POST and
                    hasattr(request.POST.get('logo'), 'file') and
                    request.POST.get('logo').file):
                logo.store(page, request.POST.get('logo').file)
        except Exception, e:
            h.flash(_(u"errors while uploading image: %s") % unicode(e),
                    'error')
            log.debug(e)

        if can.watch.create():
            watchlist.set_watch(page, self.form_result.get('watch'))
        event.emit(event.T_PAGE_CREATE, c.user, instance=c.instance,
                   topics=[page], page=page, rev=page.head)
        redirect(came_from)

    @RequireInstance
    @validate(schema=PageEditForm(), form='edit', post_only=False, on_get=True)
    def edit(self, id, variant=None, text=None, branch=False, errors={},
             format=u'html'):
        c.page, c.text, c.variant = self._get_page_and_text(id, variant, text)
        c.variant = request.params.get("variant", c.variant)
        c.proposal = request.params.get("proposal")
        c.formatting = request.params.get("formatting", False)
        c.sectionpage = request.params.get("sectionpage", True)
        c.allow_comment = request.params.get("allow_comment", False)
        c.allow_selection = request.params.get("allow_selection", False)
        c.always_show_original = request.params.get("always_show_original",
                                                    False)
        c.branch = branch
        c.container = c.page.function == c.page.CONTAINER
        c.abstract = request.params.get("abstract")

        c.section = 'section_parent' in request.params
        if c.section:
            c.parent = get_entity_or_abort(
                model.Page, request.params.get(u'section_parent'))

        if branch or c.variant is None:
            c.variant = ""

        require.norm.edit(c.page, c.variant)

        # all available categories
        c.categories = model.CategoryBadge.all(c.instance, include_global=True)

        # categories for this page
        # (single category not assured in db model)
        c.category = c.page.category

        if logo.exists(c.page):
            c.logo = '<img src="%s" />' % h.logo_url(c.page, 48)

        defaults = dict(request.params)
        if 'watch' not in defaults:
            defaults['watch'] = h.find_watch(c.page)

        if branch and c.text is None:
            c.text = c.page.head.text

        if c.came_from != u'':
            c.came_from = c.came_from
        elif c.section:
            c.came_from = h.entity_url(c.parent,
                                       anchor="subpage-%i" % c.page.id)
        else:
            c.came_from = h.entity_url(c.text)

        c.text_rows = libtext.text_rows(c.text)
        c.left = c.page.head
        html = render('/page/edit.html', overlay=format == u'overlay',
                      overlay_size=OVERLAY_SMALL)
        return htmlfill.render(html, defaults=defaults,
                               errors=errors, force_defaults=False)

    @RequireInstance
    @RequireInternalRequest(methods=['POST'])
    def update(self, id, variant=None, text=None, format='html'):
        c.page, c.text, c.variant = self._get_page_and_text(id, variant, text)
        branch = False
        try:
            class state_(object):
                page = c.page

            # branch is validated on its own, since it needs to be
            # carried to the
            # error page.
            branch_val = validators.StringBool(not_empty=False,
                                               if_empty=False,
                                               if_missing=False)
            branch = branch_val.to_python(request.params.get('branch'))

            self.form_result = PageUpdateForm().to_python(request.params,
                                                          state=state_())

            # delete the logo if the button was pressed and exit
            if 'delete_logo' in self.form_result:
                updated = logo.delete(c.page)
                h.flash(_(u'The logo has been deleted.'), 'success')
                redirect(h.entity_url(c.page))

            try:
                # fixme: show image errors in the form
                if ('logo' in request.POST and
                        hasattr(request.POST.get('logo'), 'file') and
                        request.POST.get('logo').file):
                    logo.store(c.page, request.POST.get('logo').file)
            except Exception, e:
                model.meta.Session.rollback()
                h.flash(unicode(e), 'error')
                log.debug(e)
                return self.edit(id, variant=c.variant, text=c.text.id,
                                 branch=branch, format=format)

            parent_text = self.form_result.get("parent_text")
            if ((branch or
                 parent_text.variant != self.form_result.get("variant")) and
                    self.form_result.get("variant") in c.page.variants):
                msg = (_("Variant %s is already present, cannot branch.") %
                       self.form_result.get("variant"))
                raise Invalid(msg, branch, state_(),
                              error_dict={'variant': msg})
        except Invalid, i:
            return self.edit(id, variant=c.variant, text=c.text.id,
                             branch=branch, errors=i.unpack_errors(),
                             format=format)

        c.variant = self.form_result.get("variant")
        require.norm.edit(c.page, c.variant)

        if parent_text.page != c.page:
            return ret_abort(_("You're trying to update to a text which is "
                               "not part of this pages history"),
                             code=400, format=format)

        if can.variant.edit(c.page, model.Text.HEAD):
            parent_page = self.form_result.get("parent_page", NoPage)
            if parent_page != NoPage and parent_page != c.page:
                c.page.parent = parent_page

        if can.page.manage(c.page):
            c.page.milestone = self.form_result.get('milestone')

            categories = self.form_result.get('category')
            category = categories[0] if categories else None
            c.page.set_category(category, c.user)

            c.page.formatting = self.form_result.get('formatting')
            c.page.sectionpage = self.form_result.get('sectionpage')
            c.page.allow_comment = self.form_result.get('allow_comment')
            c.page.allow_selection = self.form_result.get('allow_selection')
            c.page.always_show_original = self.form_result.get(
                'always_show_original')

        if not branch and c.variant != parent_text.variant \
                and parent_text.variant != model.Text.HEAD:
            c.page.rename_variant(parent_text.variant, c.variant)

        text = model.Text.create(c.page, c.variant, c.user,
                                 self.form_result.get("title"),
                                 self.form_result.get("text"),
                                 parent=parent_text)

        target = text
        proposal = self.form_result.get("proposal")
        if proposal is not None and can.selection.create(proposal):
            target = model.Selection.create(proposal, c.page, c.user,
                                            variant=c.variant)
            poll = target.variant_poll(c.variant)
            if poll and can.poll.vote(poll):
                decision = democracy.Decision(c.user, poll)
                decision.make(model.Vote.YES)
                model.Tally.create_from_poll(poll)

        if (config.get_bool('adhocracy.page.allow_abstracts')
                and c.instance.page_index_as_tiles
                and not c.page.is_section()):
            c.page.abstract = self.form_result.get('abstract')

        model.meta.Session.commit()
        if can.watch.create():
            watchlist.set_watch(c.page, self.form_result.get('watch'))
        event.emit(event.T_PAGE_EDIT, c.user, instance=c.instance,
                   topics=[c.page], page=c.page, rev=text)
        if c.came_from != u'':
            redirect(c.came_from)
        else:
            redirect(h.entity_url(text))

    @classmethod
    def _diff_details(cls, left, right, formatting):
        left_text = left.text.strip() if left.text else ''
        right_text = right.text.strip() if right.text else ''
        has_changes = ((left_text != right_text))

        title = right.title
        if formatting:
            text = render_text(right.text)
        else:
            text = render_line_based(right)
        text_diff = norm_texts_inline_compare(left, right)
        title_diff = page_titles_compare(left, right)

        return dict(title=title, text=text, title_diff=title_diff,
                    text_diff=text_diff, has_changes=has_changes,
                    is_head=(right.variant == model.Text.HEAD))

    @classmethod
    def _selection_urls(cls, selection):
        urls = {}
        for (variant, poll) in selection.variant_polls:
            urls[variant] = {
                'votes': h.entity_url(poll, member="votes"),
                'poll_widget': h.entity_url(poll, member="widget.big")}
        return {'urls': urls}

    @classmethod
    def _selections_details(cls, page, variant, current_selection=None):
        try:
            selections = model.Selection.by_variant(page, variant)
        except IndexError:
            selections = []
        return [cls._selection_details(selection, variant,
                                       current_selection=current_selection)
                for selection in selections]

    @classmethod
    def _selection_details(cls, selection, variant, current_selection=None):
        try:
            score = selection.variant_poll(variant).tally.score
        except:
            score = 0
        rendered_score = "%+d" % score
        current = False
        if current_selection is not None:
            current = selection.id == current_selection.id
        return {'score': score,
                'rendered_score': rendered_score,
                'selection_id': selection.id,
                'proposal_title': selection.proposal.title,
                'proposal_text': render_text(
                    selection.proposal.description.head.text),
                'proposal_url': h.selection.url(selection),
                'proposal_creator_name': selection.proposal.creator.name,
                'proposal_creator_url': h.entity_url(
                    selection.proposal.creator),
                'proposal_create_time': h.datetime_tag(
                    selection.proposal.create_time),
                'proposal_edit_url': h.entity_url(
                    selection.proposal, member='edit'),
                'proposal_can_edit': can.proposal.edit(selection.proposal),
                'proposal_delete_url': h.entity_url(selection.proposal,
                                                    member='ask_delete'),
                'proposal_can_delete': can.proposal.delete(selection.proposal),
                'current': current,
                }

    @classmethod
    def _variant_details(cls, page, variant):
        '''
        Return details for a variant including diff information
        and details about the proposals that selected this variant.
        '''
        head_text = page.head
        variant_text = page.variant_head(variant)
        details = cls._diff_details(head_text, variant_text, page.formatting)

        # Replace items coming from diff_details for the UI
        messages = (('text', _('<i>(No text)</i>')),
                    ('title', _('<i>(No title)</i>')),
                    ('text_diff', _('<i>(No differences)</i>')),
                    ('title_diff', _('<i>(No differences)</i>')))
        for (key, message) in messages:
            if details[key].strip() == '':
                details[key] = message

        selections = cls._selections_details(page, variant)
        if variant == model.Text.HEAD:
            is_head = True
            votewidget_url = ''
        else:
            is_head = False
            try:
                selection = model.Selection.by_variant(page, variant)[0]
                votewidget_url = h.entity_url(
                    selection.proposal.rate_poll,
                    member="widget.big")
            except IndexError:
                votewidget_url = ''
        details.update(
            {'variant': variant,
             'display_title': cls._variant_display_title(variant),
             'history_url': h.entity_url(variant_text, member='history'),
             'history_count': len(variant_text.history),
             'selections': selections,
             'num_selections': len(selections),
             'is_head': is_head,
             'can_edit': can.variant.edit(page, variant),
             'edit_url': h.entity_url(variant_text, member='edit'),
             'votewidget_url': votewidget_url})
        return details

    @classmethod
    def _variant_display_title(cls, variant):
        if variant == model.Text.HEAD:
            return _('Original version')
        return _(u'Variant: "%s"') % variant

    @classmethod
    def _variant_item(cls, page, variant):
        '''
        Return a `dict` with information about the variant.
        '''
        is_head = (variant == model.Text.HEAD)
        title = _('Original Version') if is_head else variant
        return {'href': h.page.page_variant_url(page, variant=variant),
                'title': title,
                'display_title': title,  # bbb
                'is_head': is_head,
                'variant': variant}

    @classmethod
    def _variant_items(self, page, selection=None):
        '''
        Return a `list` of `dicts` with information about the variants.
        '''
        items = []
        for variant in page.variants:
            if selection and (variant not in selection.variants):
                continue
            item = self._variant_item(page, variant)
            items.append(item)

        return items

    @classmethod
    def _insert_variant_score_and_sort(self, items, score_func):
        '''
        Insert the score into the items and sort the variant items based
        on their *score* with mode.Text.HEAD as the first item.

        score_func is a method that receives the item as the only
        argument.
        '''
        head_item = None
        other_items = []
        for item in items:
            if item['variant'] == model.Text.HEAD:
                item['score'] = None
                item['rendered_score'] = ''
                head_item = item
            else:
                score = score_func(item)
                item['score'] = score
                item['rendered_score'] = '%+d' % score
                other_items.append(item)

        items = sorted(other_items, key=itemgetter('score'), reverse=True)
        items.insert(0, head_item)
        return items

    @RequireInstance
    def show(self, id, variant=None, text=None, format='html',
             amendment=False):
        if amendment:
            # variant may actually be a proposal id
            proposal = model.Proposal.find(variant)
            if proposal is not None and proposal.is_amendment:
                variant = proposal.selection.selected

        c.page, c.text, c.variant = self._get_page_and_text(id, variant, text)
        require.page.show(c.page)

        c.overlay = format == 'overlay'
        c.amendment = amendment

        if c.amendment and not c.page.allow_selection:
            return ret_abort(
                _("Page %s does not allow selections") % c.page.title,
                code=400, format=format)

        # Error handling and json api
        if c.text.variant != c.variant:
            abort(404, _("Variant %s does not exist!") % c.variant)
        if format == 'json':
            return render_json(c.page.to_dict(text=c.text))

        c.category = c.page.category
        # variant details and returning them as json when requested.
        c.variant_details = self._variant_details(c.page, c.variant)
        if 'variant_json' in request.params:
            return render_json(c.variant_details)
        c.variant_details_json = json.dumps(c.variant_details, indent=4)

        # Make a list of variants to render the vertical tab navigation
        variant_items = self._variant_items(c.page)

        def get_score(item):
            selections = model.Selection.by_variant(c.page,
                                                    item['variant'])
            if len(selections):
                return selections[0].proposal.rate_poll.tally.score
            else:
                return 0

        variant_items = self._insert_variant_score_and_sort(variant_items,
                                                            get_score)

        # filter out all but the highest rated variant from a proposal
        c.variant_items = []
        selections = []
        for item in variant_items:
            variant = item['variant']
            if variant == model.Text.HEAD:
                c.variant_items.append(item)
                continue
            selections_ = model.Selection.by_variant(c.page, variant)
            if not selections_:
                log.warning('continue - no selection: %s' % variant)
                continue
            selection = selections_[0]
            if selection not in selections:
                selections.append(selection)
                c.variant_items.append(item)

        # Metadata and subpages pager
        sorts = {_("oldest"): sorting.entity_oldest,
                 _("newest"): sorting.entity_newest,
                 _("alphabetically"): sorting.delegateable_title}
        c.subpages_pager = pager.NamedPager(
            'subpages', c.page.subpages,
            (tiles.page.row
             if c.page.function == model.Page.CONTAINER
             else tiles.page.smallrow),
            sorts=sorts, default_sort=sorting.delegateable_title)
        self._common_metadata(c.page, c.text)
        c.tutorial_intro = _('tutorial_norm_show_tab')
        c.tutorial = 'page_show'

        if c.page.function == c.page.CONTAINER:
            return render("/page/show_container.html")
        elif not c.amendment and c.page.is_sectionpage():
            return render("/page/show_sectionpage.html",
                          overlay=(format == 'overlay'))
        else:
            return render("/page/show.html",
                          overlay=(format == 'overlay'))

    @RequireInstance
    def history(self, id, variant=model.Text.HEAD, text=None, format='html'):
        c.page, c.text, c.variant = self._get_page_and_text(id, variant, text)
        require.page.show(c.page)
        if c.text is None:
            h.flash(_("No such text revision."), 'notice')
            redirect(h.entity_url(c.page))
        c.texts_pager = pager.NamedPager(
            'texts', c.text.history, tiles.text.history_row, count=10,
            sorts={},
            default_sort=sorting.entity_newest)

        if format == 'json':
            return render_json(c.texts_pager)
        c.tile = tiles.page.PageTile(c.page)
        self._common_metadata(c.page, c.text)

        if format == 'ajax':
            return c.texts_pager.here()
        elif format == 'overlay':
            return render('/page/history.html', overlay=True)
        else:
            return render('/page/history.html')

    @RequireInstance
    def comments(self, id, variant=model.Text.HEAD, text=None, format=None):
        c.page, c.text, c.variant = self._get_page_and_text(id, variant, text)
        require.page.show(c.page)
        if not c.page.allow_comment:
            return ret_abort(
                _("Page %s does not allow comments") % c.page.title,
                code=400, format=format)

        if c.text is None:
            h.flash(_("No such text revision."), 'notice')
            redirect(h.entity_url(c.page))
        self._common_metadata(c.page, c.text)
        c.came_from = ''

        if format == 'ajax':
            return tiles.comment.list(c.page)
        elif format == 'overlay':
            c.came_from = h.entity_url(c.page, member='comments') + '.overlay'
            return render('/page/comments.html', overlay=True,
                          overlay_size=OVERLAY_SMALL)
        else:
            return render('/page/comments.html')

    @RequireInstance
    @validate(schema=PageDiffForm(), form='bad_request', post_only=False,
              on_get=True)
    def diff(self):
        left = self.form_result.get('left')
        right = self.form_result.get('right')
        require.page.show(left.page)
        require.page.show(right.page)
        options = [right.page.variant_head(v) for v in right.page.variants]
        return self._differ(left, right, options=options)

    def _differ(self, left, right, options=None):
        if left == right:
            h.flash(_("Cannot compare identical text revisions."), 'notice')
            redirect(h.entity_url(right))
        c.left, c.right = (left, right)
        c.left_options = options
        if c.left.page != c.right.page:
            h.flash(_("Cannot compare versions of different texts."), 'notice')
            redirect(h.entity_url(c.right))
        c.tile = tiles.page.PageTile(c.right.page)
        self._common_metadata(c.right.page, c.right)
        return render("/page/diff.html")

    @RequireInstance
    def ask_purge(self, id, variant):
        c.page, c.text, c.variant = self._get_page_and_text(id, variant, None)
        require.variant.delete(c.page, c.variant)
        c.tile = tiles.page.PageTile(c.page)
        return render("/page/ask_purge.html")

    @RequireInstance
    @RequireInternalRequest()
    def purge(self, id, variant):
        c.page, c.text, c.variant = self._get_page_and_text(id, variant, None)
        require.variant.delete(c.page, c.variant)
        c.page.purge_variant(c.variant)
        model.meta.Session.commit()
        # event.emit(event.T_PAGE_DELETE, c.user, instance=c.instance,
        #            topics=[c.page], page=c.page)
        h.flash(_("The variant %s has been deleted.") % c.variant,
                'success')
        redirect(h.entity_url(c.page))

    @RequireInstance
    def ask_purge_history(self, id, text):
        c.page, c.text, c.variant = self._get_page_and_text(id, None, text)
        require.page.delete_history(c.page)
        if c.text.valid_child() is None and c.text.valid_parent() is None:
            h.flash(_("Cannot delete, if there's only one version"), 'error')
            return redirect(h.entity_url(c.text))
        return render("/page/ask_purge_history.html")

    @RequireInstance
    @RequireInternalRequest()
    def purge_history(self, id, text):
        c.page, c.text, c.variant = self._get_page_and_text(id, None, text)
        require.page.delete_history(c.page)
        if c.text.valid_child() is None and c.text.valid_parent() is None:
            h.flash(_("Cannot delete, if there's only one version"), 'error')
            return redirect(h.entity_url(c.text))
        c.text.delete()
        model.meta.Session.commit()
        h.flash(_("The selected version has been deleted."), 'success')
        redirect(h.entity_url(c.page))

    @RequireInstance
    def ask_delete(self, id, format="html"):
        c.page = get_entity_or_abort(model.Page, id)
        require.page.delete(c.page)
        c.tile = tiles.page.PageTile(c.page)

        c.section = u'section_parent' in request.params
        if c.section:
            c.parent = get_entity_or_abort(
                model.Page, request.params.get(u'section_parent'))
            c.came_from = h.entity_url(c.parent)
        else:
            c.came_from = h.entity_url(c.page.instance)

        return render("/page/ask_delete.html", overlay=(format == u'overlay'))

    @RequireInstance
    @RequireInternalRequest()
    def delete(self, id):
        c.page = get_entity_or_abort(model.Page, id)
        require.page.delete(c.page)
        c.page.delete()
        model.meta.Session.commit()
        event.emit(event.T_PAGE_DELETE, c.user, instance=c.instance,
                   topics=[c.page], page=c.page)
        h.flash(_("The page %s has been deleted.") % c.page.title,
                'success')
        redirect(c.came_from)

    def _get_page_and_text(self, id, variant, text):
        page = get_entity_or_abort(model.Page, id)
        _text = page.head
        if text is not None:
            _text = get_entity_or_abort(model.Text, text)
            if _text.page != page or (variant and _text.variant != variant):
                abort(404, _("Invalid text ID %s for this page/variant!") %
                      text)
            variant = _text.variant
        elif variant is not None:
            _text = page.variant_head(variant)
            if _text is None:
                _text = page.head
        else:
            variant = _text.variant
        return (page, _text, variant)

    def _common_metadata(self, page, text):
        if text and text.text and len(text.text):
            h.add_meta("description",
                       libtext.meta_escape(text.text, markdown=False)[0:160])
        tags = page.tags
        if len(tags):
            h.add_meta("keywords", ", ".join([k.name for (k, v) in tags]))
        h.add_meta("dc.title",
                   libtext.meta_escape(page.title, markdown=False))
        h.add_meta("dc.date",
                   page.create_time.strftime("%Y-%m-%d"))
        h.add_meta("dc.author",
                   libtext.meta_escape(text.user.name, markdown=False))

    @RequireInstance
    def logo(self, id, y, x=None):
        page = get_entity_or_abort(model.Page, id)
        return render_logo(page, y, x=x)
