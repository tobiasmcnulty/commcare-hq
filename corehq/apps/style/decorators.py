from functools import wraps
from corehq.apps.style.utils import set_bootstrap_version3
from crispy_forms.utils import set_template_pack


def use_bootstrap3(view_func):
    """Use this decorator on the dispatch method of a TemplateView subclass
    to enable Bootstrap3 features for the included template. This makes sure
    that all crispy forms are in Boostrap3 mode, for instance.

    Example:

    @use_bootstrap3
    def dispatch(request, *args, **kwargs):
        return super(MyView, self).dispatch(request, *args, **kwargs)
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        # set bootstrap version in thread local
        set_bootstrap_version3()
        # set crispy forms template in thread local
        set_template_pack('bootstrap3')
        return view_func(request, *args, **kwargs)
    return _wrapped


def use_select2(view_func):
    """Use this decorator on the dispatch method of a TemplateView subclass
    to enable the inclusion of the select2 js library at the base template.

    Example:

    @use_select2
    def dispatch(request, *args, **kwargs):
        return super(MyView, self).dispatch(request, *args, **kwargs)
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        request.use_select2 = True
        return view_func(request, *args, **kwargs)
    return _wrapped


def use_select2_v4(view_func):
    """Use this decorator on the dispatch method of a TemplateView subclass
    to enable the inclusion of the 4.0 Version of select2 js library at
    the base template. (4.0 is still in testing phase)

    Example:

    @use_select2_v4
    def dispatch(request, *args, **kwargs):
        return super(MyView, self).dispatch(request, *args, **kwargs)
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        request.use_select2_v4 = True
        return view_func(request, *args, **kwargs)
    return _wrapped


def use_knockout_js():
    def decorate(fn):
        """
        Decorator to Toggle on the use of bootstrap 3.
        """
        def wrapped(request, *args, **kwargs):
            request.use_knockout_js = True
            return fn(request, *args, **kwargs)
        return wrapped
    return decorate
