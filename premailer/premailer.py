import codecs
from lxml import etree
from lxml.cssselect import CSSSelector
import os
import re
import urllib
import urlparse


__version__ = '1.14'
__all__ = ['PremailerError', 'Premailer', 'transform']


class PremailerError(Exception):
    pass


grouping_regex = re.compile('([:\-\w]*){([^}]+)}')


def _merge_styles(old, new, class_=''):
    """
    if ::
      old = 'font-size:1px; color: red'
    and ::
      new = 'font-size:2px; font-weight: bold'
    then ::
      return 'color: red; font-size:2px; font-weight: bold'

    In other words, the new style bits replace the old ones.

    The @class_ parameter can be something like ':hover' and if that
    is there, you split up the style with '{...} :hover{...}'
    Note: old could be something like '{...} ::first-letter{...}'

    """
    news = {}
    for k, v in [x.strip().split(':', 1) for x in new.split(';') if x.strip()]:
        news[k.strip()] = v.strip()

    groups = {}
    grouped_split = grouping_regex.findall(old)
    if grouped_split:
        for old_class, old_content in grouped_split:
            olds = {}
            for k, v in [x.strip().split(':', 1) for
                         x in old_content.split(';') if x.strip()]:
                olds[k.strip()] = v.strip()
            groups[old_class] = olds
    else:
        olds = {}
        for k, v in [x.strip().split(':', 1) for
                     x in old.split(';') if x.strip()]:
            olds[k.strip()] = v.strip()
        groups[''] = olds

    # Perform the merge
    merged = news
    for k, v in groups.get(class_, {}).items():
        if k not in merged:
            merged[k] = v
    groups[class_] = merged

    if len(groups) == 1:
        return '; '.join(['%s:%s' % (k, v) for
                          (k, v) in groups.values()[0].items()])
    else:
        all = []
        for class_, mergeable in sorted(groups.items(),
                                        lambda x, y: cmp(x[0].count(':'),
                                                         y[0].count(':'))):
            all.append('%s{%s}' % (class_,
                                   '; '.join(['%s:%s' % (k, v)
                                              for (k, v)
                                              in mergeable.items()])))
        return ' '.join([x for x in all if x != '{}'])


_css_comments = re.compile(r'/\*.*?\*/', re.MULTILINE | re.DOTALL)
_regex = re.compile('((.*?){(.*?)})', re.DOTALL | re.M)
_semicolon_regex = re.compile(';(\s+)')
_colon_regex = re.compile(':(\s+)')
_importants = re.compile('\s*!important')
# These selectors don't apply to all elements. Rather, they specify
# which elements to apply to.
FILTER_PSEUDOSELECTORS = [':last-child', ':first-child', 'nth-child']


class Premailer(object):

    def __init__(self, xml, base_url=None,
                 preserve_internal_links=False,
                 exclude_pseudoclasses=False,
                 keep_style_tags=False,
                 include_star_selectors=False,
                 remove_classes=True,
                 strip_important=True,
                 external_styles=None):
        self.xml = xml
        self.base_url = base_url
        self.preserve_internal_links = preserve_internal_links
        self.exclude_pseudoclasses = exclude_pseudoclasses
        # whether to delete the <style> tag once it's been processed
        self.keep_style_tags = keep_style_tags
        self.remove_classes = remove_classes
        # whether to process or ignore selectors like '* { foo:bar; }'
        self.include_star_selectors = include_star_selectors
        if isinstance(external_styles, basestring):
            external_styles = [external_styles]
        self.external_styles = external_styles
        self.strip_important = strip_important

    def _parse_style_rules(self, css_body):
        leftover = []
        rules = []
        css_body = _css_comments.sub('', css_body)
        for each in _regex.findall(css_body.strip()):
            __, selectors, bulk = each

            bulk = _semicolon_regex.sub(';', bulk.strip())
            bulk = _colon_regex.sub(':', bulk.strip())
            if bulk.endswith(';'):
                bulk = bulk[:-1]
            for selector in [x.strip() for
                             x in selectors.split(',') if x.strip() and
                             not x.strip().startswith('@')]:
                if (':' in selector and self.exclude_pseudoclasses and
                    ':' + selector.split(':', 1)[1]
                        not in FILTER_PSEUDOSELECTORS):
                    # a pseudoclass
                    leftover.append((selector, bulk))
                    continue
                elif selector == '*' and not self.include_star_selectors:
                    continue

                rules.append((selector, bulk))

        return rules, leftover

    def transform(self, pretty_print=True):
        """change the self.xml and return it with CSS turned into style
        attributes.
        """
        if etree is None:
            return self.xml

        parser = etree.XMLParser()
        stripped = self.xml.strip()
        tree = etree.fromstring(stripped, parser).getroottree()
        page = tree.getroot()
        # lxml inserts a doctype if none exists, so only include it in
        # the root if it was in the original xml.
        root = tree if stripped.startswith(tree.docinfo.doctype) else page

        if page is None:
            print repr(self.xml)
            raise PremailerError("Could not parse the xml")
        assert page is not None

        ##
        ## style selectors
        ##

        rules = []

        for style in CSSSelector('style')(page):
            these_rules, these_leftover = self._parse_style_rules(style.text)
            rules.extend(these_rules)
			
            #print(these_rules) #### Test

            parent_of_style = style.getparent()
            if these_leftover:
                style.text = '\n'.join(['%s {%s}' % (k, v) for
                                        (k, v) in these_leftover])
            elif not self.keep_style_tags:
                parent_of_style.remove(style) 
				#### seems to be wrong: we should check it only if we do not have all other cascading kinds of styles
				
		###
        # for style in CSSSelector('class')(page):
            # these_rules, these_leftover = self._parse_style_rules(style.text)
            # rules.extend(these_rules)
            
            # print(these_rules) #### Test
		###

        if self.external_styles:
            for stylefile in self.external_styles:
                if stylefile.startswith('http://'):
                    css_body = urllib.urlopen(stylefile).read()
                elif os.path.exists(stylefile):
                    try:
                        f = codecs.open(stylefile)
                        css_body = f.read()
                    finally:
                        f.close()
                else:
                    raise ValueError(u"Could not find external style: %s" %
                                     stylefile)
                these_rules, these_leftover = self._parse_style_rules(css_body)
                rules.extend(these_rules)

        first_time = []
        first_time_styles = []
        for selector, style in rules:
            new_selector = selector
            class_ = ''
            if ':' in selector:
                new_selector, class_ = re.split(':', selector, 1)
                class_ = ':%s' % class_
            # Keep filter-type selectors untouched.
            if class_ in FILTER_PSEUDOSELECTORS:
                class_ = ''
            else:
                selector = new_selector

            sel = CSSSelector(selector)
            for item in sel(page):
                old_style = item.attrib.get('style', '')
                if not item in first_time:
                    if old_style:
                        new_style = _merge_styles(style, old_style, class_)
                    else:
                        new_style = _merge_styles(old_style, style, class_)
                    first_time.append(item)
                    first_time_styles.append((item, old_style))
                else:
                    new_style = _merge_styles(old_style, style, class_)
                item.attrib['style'] = new_style
                #### self._style_to_basic_html_attributes(item, new_style, force=True)

        # Re-apply initial inline styles.
        for item, inline_style in first_time_styles:
            old_style = item.attrib.get('style', '')
            if not inline_style:
                continue
            new_style = _merge_styles(old_style, inline_style, class_)
            item.attrib['style'] = new_style
            #### self._style_to_basic_html_attributes(item, new_style, force=True)

        if self.remove_classes:
            # now we can delete all 'class' attributes
            for item in page.xpath('//@class'):
                parent = item.getparent()
                del parent.attrib['class']

        ##
        ## URLs
        ##
        if self.base_url:
            for attr in ('href', 'src'):
                for item in page.xpath("//@%s" % attr):
                    parent = item.getparent()
                    if attr == 'href' and self.preserve_internal_links \
                           and parent.attrib[attr].startswith('#'):
                        continue
                    parent.attrib[attr] = urlparse.urljoin(self.base_url,
                                                           parent.attrib[attr])

        out = etree.tostring(root, method="xml", pretty_print=pretty_print)
        if self.strip_important:
            out = _importants.sub('', out)
        return out


def transform(xml, base_url=None):
    return Premailer(xml, base_url=base_url, keep_style_tags=True).transform()


if __name__ == '__main__':
    xml = """<html>
        <head>
        <title>Test</title>
        <style>
        h1, h2 { color:red; }
        strong {
          text-decoration:none
          }
        p { font-size:2px }
        p.footer { font-size: 1px}
        </style>
        </head>
        <body>
        <h1>Hi!</h1>
        <p><strong>Yes!</strong></p>
        <p class="footer" style="color:red">Feetnuts</p>
        </body>
        </html>"""
    p = Premailer(xml)
    print p.transform()
