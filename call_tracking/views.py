from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.urlresolvers import reverse_lazy
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render_to_response, redirect
from django.template.context_processors import csrf
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import TemplateView
from django.views.generic.edit import UpdateView
from twilio import twiml

from .forms import AreaCodeForm, PurchaseNumberForm
from .models import LeadSource, Lead
from .utils import search_phone_numbers, purchase_phone_number, get_twilio_application


# Home page view and JSON views to power the charts
class HomePageView(TemplateView):
    """Renders the home page template with some additional context"""

    template_name = "index.html"

    def get_context_data(self, **kwargs):
        context = super(HomePageView, self).get_context_data(**kwargs)

        # Add the area code form - default to 415
        context['form'] = AreaCodeForm({'area_code': '415'})

        # Add the list of lead sources
        context['lead_sources'] = LeadSource.objects.all()
        return context

def leads_by_source(request):
    """Returns JSON data about the lead sources and how many leads they have"""
    # Invoke a LeadSource classmethod to get the data
    data = LeadSource.get_leads_per_source()

    # Return it as JSON
    return JsonResponse(data)

def leads_by_city(request):
    """Returns JSON data about the different cities leads come from"""
    # Invoke a Lead classmethod to get the data
    data = Lead.get_leads_per_city()

    # Return it as JSON
    return JsonResponse(data)


# Views for purchase number workflow
def list_numbers(request):
    """Uses the Twilio API to generate a list of available phone numbers"""
    form = AreaCodeForm(request.POST)

    if form.is_valid():
        # We received a valid area code - query the Twilio API
        area_code = form.cleaned_data['area_code']

        available_numbers = search_phone_numbers(area_code=area_code)

        # Check if there are no numbers available in this area code
        if not available_numbers:
            messages.error(request, 'There are no Twilio numbers available for area code {0}. Search for numbers in a different area code.'.format(area_code))
            return redirect('home')

        context = {}
        context.update(csrf(request))
        context['available_numbers'] = available_numbers

        return render_to_response('call_tracking/list_numbers.html', context)
    else:
        # Our area code was invalid - flash a message and redirect back home
        bad_area_code = form.data['area_code']
        messages.error(request, '{0} is not a valid area code. Please search again.'
            .format(bad_area_code))

        return redirect('home')

def purchase_number(request):
    """Purchases a new phone number using the Twilio API"""
    form = PurchaseNumberForm(request.POST)

    if form.is_valid():
        # We received a valid phone number - before we purchase it, get the
        # Twilio Application SID so all our numbers have the same voice URL
        application = get_twilio_application()

        # Purchase the phone number
        phone_number = form.cleaned_data['phone_number']
        twilio_number = purchase_phone_number(phone_number.as_e164, application.sid)

        # Save it in a new LeadSource object
        lead_source = LeadSource(incoming_number=twilio_number.phone_number)
        lead_source.save()

        messages.success(request, 'Phone number {0} has been purchased. Please add a name for this lead source.'.format(twilio_number.friendly_name))

        # Display a warning message if the Twilio Application is still using
        # example.com in its voice URL
        if 'example.com' in application.voice_url:
            messages.warning(request, 'WARNING: You <b>must</b> update the Twilio Application\'s voice URL before this number will forward calls. You can do that here: <a href="https://www.twilio.com/user/account/apps/{0}" target="_blank">https://www.twilio.com/user/account/apps/{0}</a>.'.format(application.sid))

        # Redirect to edit lead page
        return redirect('edit_lead_source', pk=lead_source.pk)
    else:
        # In the unlikely event of an error, redirect to the home page
        bad_phone_number = form.data['phone_number']
        messages.error(request, '{0} is not a valid phone number. Please search again.'
            .format(bad_phone_number))

        return redirect('home')

class LeadSourceUpdateView(SuccessMessageMixin, UpdateView):
    """Powers a form to edit Lead Sources"""

    model = LeadSource
    fields = ['name', 'forwarding_number']
    success_url = reverse_lazy('home')
    success_message = 'Lead source successfully updated.'


# View used by Twilio API to connect callers to the right forwarding
# number for that lead source
@csrf_exempt
def forward_call(request):
    """Connects an incoming call to the correct forwarding number"""
    # First look up the lead source
    source = LeadSource.objects.get(incoming_number=request.POST['Called'])

    # Create a lead entry for this call
    lead = Lead(
        source=source,
        phone_number=request.POST['Caller'],
        city=request.POST['CallerCity'],
        state=request.POST['CallerState'])
    lead.save()

    # Respond with some TwiML that connects the caller to the forwarding_number
    r = twiml.Response()
    r.dial(source.forwarding_number.as_e164)

    return HttpResponse(r)
