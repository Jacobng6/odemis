from odemis import model
sem = model.getComponent(role="SEM")

state = sem.get_beam_is_on

if state = True:
    off_confirm = input("Beam is on. Turn beam off? y/n: ")
    if off_confirm = "y" or off_confirm = "Y":
        sem.set_beam_power(False)
elif state = False:
    on_conform = input("Beam is off. Turn beam on? y/n: ")
    if on_confirm = "y" or on_confirm = "Y":
        sem.set_beam_power(True)

state = sem.get_beam_is_on
if state = True:
    print("The beam is now on")
elif state = False:
    print("The beam is now off")
    
    