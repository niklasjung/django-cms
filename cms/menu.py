# -*- coding: utf-8 -*-
from menus.menu_pool import menu_pool
from menus.base import Menu, NavigationNode, Modifier
from cms.utils import get_language_from_request
from cms.utils.moderator import get_page_queryset, get_title_queryset
from django.conf import settings
from django.contrib.sites.models import Site
from cms.utils.i18n import get_fallback_languages
from cms.apphook_pool import apphook_pool
from cms.models.titlemodels import Title

def page_to_node(page, home, cut):
    '''
    Transform a CMS page into a navigation node.
    
    page: the page you wish to transform
    home: a reference to the "home" page (the page with tree_id=1)
    cut: Should we cut page from it's parent pages? This means the node will not
         have a parent anymore.
    '''
    # Theses are simple to port over, since they are not calculated.
    # Other attributes will be added conditionnally later.
    attr = {'soft_root':page.soft_root,
            'auth_required':page.login_required,
            'reverse_id':page.reverse_id,}
    
    parent_id = page.parent_id
    # Should we cut the Node from its parents?
    if home and page.parent_id == home.pk and cut:
        parent_id = None
    
    # possible fix for a possible problem
    #if parent_id and not page.parent.get_calculated_status():
    #    parent_id = None # ????
    
    if page.limit_visibility_in_menu == None:
        attr['visible_for_authenticated'] = True
        attr['visible_for_anonymous'] = True
    else:
        attr['visible_for_authenticated'] = page.limit_visibility_in_menu == 1
        attr['visible_for_anonymous'] = page.limit_visibility_in_menu == 2
        
    if page.pk == home.pk:
        attr['is_home'] = True

    # Extenders can be either navigation extenders or from apphooks.
    extenders = [] 
    if page.navigation_extenders:
        extenders.append(page.navigation_extenders)
    # Is this page an apphook? If so, we need to handle the apphooks's nodes
    try:
        app_name = page.get_application_urls(fallback=False)
    except Title.DoesNotExist:
        app_name = None
    if app_name: # it means it is an apphook
        app = apphook_pool.get_apphook(app_name)
        for menu in app.menus:
            extenders.append(menu.__name__)
    
    if extenders:
        attr['navigation_extenders'] = extenders
    
    # Do we have a redirectURL?
    attr['redirect_url'] = page.get_redirect()  # save redirect URL if any
    
    # Now finally, build the NavigationNode object and return it.
    ret_node = NavigationNode(
        page.get_menu_title(), 
        page.get_absolute_url(), 
        page.pk, 
        parent_id, 
        attr=attr,
        visible=page.in_navigation,
    )
    return ret_node

class CMSMenu(Menu):
    
    def get_nodes(self, request):
        page_queryset = get_page_queryset(request)
        site = Site.objects.get_current()
        lang = get_language_from_request(request)
        
        filters = {
            'site':site,
        }
        
        if settings.CMS_HIDE_UNTRANSLATED:
            filters['title_set__language'] = lang
            
        pages = page_queryset.published().filter(**filters).order_by("tree_id", "lft")
        
        ids = []
        nodes = []
        first = True
        home_cut = False
        home_children = []
        home = None
        actual_pages = []

        for page in pages:
            # Pages are ordered by tree_id, therefore the first page is the root
            # of the page tree (a.k.a "home")
            if not page.has_view_permission(request):
                # Don't include pages the user doesn't have access to
                continue
            if not home:
                home = page
            page.home_pk_cache = home.pk
            if first and page.pk != home.pk:
                home_cut = True
            elif not settings.CMS_PUBLIC_FOR == 'all':
                continue
            if (page.parent_id == home.pk or page.parent_id in home_children) and home_cut:
                page.home_cut_cache = True 
                home_children.append(page.pk)
            if (page.pk == home.pk and home.in_navigation) or page.pk != home.pk:
                first = False
            ids.append(page.id)
            actual_pages.append(page)

        titles = list(get_title_queryset(request).filter(page__in=ids, language=lang))
        for page in actual_pages: # add the title and slugs and some meta data
            for title in titles:
                if title.page_id == page.pk:
                    if not hasattr(page, "title_cache"):
                        page.title_cache = {}
                    page.title_cache[title.language] = title
                    nodes.append(page_to_node(page, home, home_cut))
                    ids.remove(page.pk)

        if ids: # get fallback languages
            fallbacks = get_fallback_languages(lang)
            for l in fallbacks:
                titles = list(get_title_queryset(request).filter(page__in=ids, language=l))
                for title in titles:
                    for page in actual_pages: # add the title and slugs and some meta data
                        if title.page_id == page.pk:
                            if not hasattr(page, "title_cache"):
                                page.title_cache = {}
                            page.title_cache[title.language] = title
                            nodes.append(page_to_node(page, home, home_cut))
                            ids.remove(page.pk)
                            break
                if not ids:
                    break
        return nodes  
menu_pool.register_menu(CMSMenu)

class NavExtender(Modifier):
    def modify(self, request, nodes, namespace, root_id, post_cut, breadcrumb):
        if post_cut:
            return nodes
        exts = []
        # rearrange the parent relations
        home = None
        for node in nodes:
            if node.attr.get("is_home", False):
                home = node
            extenders = node.attr.get("navigation_extenders", None)
            if extenders:
                for ext in extenders:
                    if not ext in exts:
                        exts.append(ext)
                    for n in nodes:
                        if n.namespace == ext and not n.parent_id:# if home has nav extenders but home is not visible
                            if node.attr.get("is_home", False) and not node.visible:
                                n.parent_id = None
                                n.parent_namespace = None
                                n.parent = None
                            else:
                                n.parent_id = node.id
                                n.parent_namespace = node.namespace
                                n.parent = node
                                node.children.append(n)
        removed = []
        # find all not assigned nodes
        for menu in menu_pool.menus.items():
            if hasattr(menu[1], 'cms_enabled') and menu[1].cms_enabled and not menu[0] in exts:
                for node in nodes:
                    if node.namespace == menu[0]:
                        removed.append(node)
        if breadcrumb:  
            # if breadcrumb and home not in navigation add node
            if breadcrumb and home and not home.visible:
                home.visible = True
                if request.path == home.get_absolute_url():
                    home.selected = True
                else:
                    home.selected = False
        # remove all nodes that are nav_extenders and not assigned 
        for node in removed:
            nodes.remove(node)
        return nodes   
menu_pool.register_modifier(NavExtender)


class SoftRootCutter(Modifier):
    """
    If anyone understands this, PLEASE write a meaningful description here!
    """
    def modify(self, request, nodes, namespace, root_id, post_cut, breadcrumb):
        # only apply this modifier if we're pre-cut (since what we do is cut)
        if post_cut or not settings.CMS_SOFTROOT:
            return nodes
        selected = None
        root_nodes = []
        # find the selected node as well as all the root nodes
        for node in nodes:
            if node.selected:
                selected = node
            if not node.parent:
                root_nodes.append(node)
        
        # if we found a selected ...
        if selected:
            # and the selected is a softroot
            if selected.attr.get("soft_root", False):
                # get it's descendants
                nodes = selected.get_descendants()
                # remove the link to parent
                selected.parent = None
                # make the selected page the root in the menu
                nodes = [selected] + nodes
            else:
                # if it's not a soft root, walk ancestors (upwards!)
                nodes = self.find_ancestors_and_remove_children(selected, nodes)
            # remove child-softroots from descendants (downwards!)
            nodes = self.find_and_remove_children(selected, nodes)
        else:
            # for all nodes in root, remove child-sofroots (downwards!)
            for node in root_nodes:
                self.find_and_remove_children(node, nodes)
        return nodes   
    
    def find_and_remove_children(self, node, nodes):
        for n in node.children:
            if n.attr.get("soft_root", False):
                self.remove_children(n, nodes)
        return nodes
    
    def remove_children(self, node, nodes):
        for n in node.children:
            nodes.remove(n)
            self.remove_children(n, nodes)
        node.children = []
    
    def find_ancestors_and_remove_children(self, node, nodes):
        """
        Check ancestors of node for soft roots
        """
        if node.parent:
            if node.parent.attr.get("soft_root", False):
                nodes = node.parent.get_descendants()
                node.parent.parent = None
                nodes = [node.parent] + nodes
            else:
                nodes = self.find_ancestors_and_remove_children(node.parent, nodes)
        else:
            for n in nodes:
                if n != node and not n.parent:
                    self.find_and_remove_children(n, nodes)
        for n in node.children:
            if n != node:
                self.find_and_remove_children(n, nodes)
        return nodes
    
menu_pool.register_modifier(SoftRootCutter)
