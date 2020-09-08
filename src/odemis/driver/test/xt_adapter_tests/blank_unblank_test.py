from odemis import model
sem = model.getComponent(role="SEM")



if sem.beam_is_blanked() = True:
    unblank_confirm = input("Beam is blanked. Unblank beam? y/n: ")
    if unblank_confirm = "y" or unblank_confirm = "Y":
        sem.unblank_beam()
else:
    blank_confirm = input("Beam is not blanked. Blank beam? y/n: ")
    if blank_confirm = "y" or blank_confirm = "Y":
        sem.blank_beam()

condition = sem.beam_is_blanked()
if condition = True:
    print("The beam is now blanked")
else:
    print("The beam is now not blanked")